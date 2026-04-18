"""
ACP JSON-RPC 2.0 server over stdio.

Framing: auto-detected per connection
  - NDJSON  : each message is one JSON line  (acpx default)
  - LSP     : Content-Length header + blank line + body

The first byte received determines the mode:
  '{' → NDJSON,  'C' (Content-Length) → LSP

ACP methods handled
───────────────────
Standard lifecycle:
  initialize            → capabilities handshake
  initialized           → (notification, no response)
  shutdown / exit       → graceful teardown

Session management:
  session/new           → create (or replace) a named Gemini Chat session
  session/list          → list active sessions
  session/delete        → delete a session (nanobanana extension)

Prompting:
  session/prompt        → send a prompt; stream chunks via session/update
                          notifications; finish with session/stopped

All stdout is reserved for the JSON-RPC protocol.
Diagnostics go to stderr (or a log file when NANOBANANA_DEBUG is set).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from typing import Any

from .gemini import GeminiClient
from .session import SessionManager

# ── Logging setup (stderr only — stdout is the protocol channel) ──────────────
_log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
if os.environ.get("NANOBANANA_DEBUG"):
    _log_handlers.append(logging.FileHandler("/tmp/nanobanana.log"))

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("NANOBANANA_DEBUG") else logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)


class NanobananaServer:
    def __init__(self) -> None:
        self.sessions = SessionManager()
        self.gemini = GeminiClient()
        self._shutdown = False
        self._ndjson: bool | None = None  # None = not yet detected

    # ── Wire I/O ──────────────────────────────────────────────────────────────

    def _read(self) -> dict | None:
        """Read one JSON-RPC message from stdin.

        Auto-detects framing on the first call:
          NDJSON  — first non-empty byte is '{'
          LSP     — first line is a Content-Length header
        """
        # ── NDJSON mode ───────────────────────────────────────────────────────
        if self._ndjson is True:
            while True:
                raw = sys.stdin.buffer.readline()
                if not raw:
                    return None
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    break
            msg = json.loads(line)
            logger.debug("← %s", line)
            return msg

        # ── LSP (Content-Length) mode ─────────────────────────────────────────
        if self._ndjson is False:
            return self._read_lsp()

        # ── Auto-detect on first message ──────────────────────────────────────
        # Peek at the first non-whitespace byte without consuming it.
        while True:
            raw = sys.stdin.buffer.readline()
            if not raw:
                return None
            line = raw.decode("utf-8", errors="replace")
            stripped = line.strip()
            if stripped:
                break

        if stripped.startswith("{"):
            # First line is already a complete JSON object → NDJSON
            self._ndjson = True
            msg = json.loads(stripped)
            logger.debug("← (ndjson detected) %s", stripped)
            return msg
        else:
            # Treat the line we already read as the first header line
            self._ndjson = False
            headers: dict[str, str] = {}
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                headers[k.strip()] = v.strip()
            return self._read_lsp(headers)

    def _read_lsp(self, headers: dict[str, str] | None = None) -> dict | None:
        """Read remaining LSP headers then the body."""
        if headers is None:
            headers = {}
        while True:
            raw = sys.stdin.buffer.readline()
            if not raw:
                return None
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()

        length = int(headers.get("Content-Length", 0))
        if length == 0:
            return None

        body = sys.stdin.buffer.read(length)
        msg = json.loads(body.decode("utf-8"))
        logger.debug("← (lsp) %s", json.dumps(msg, ensure_ascii=False))
        return msg

    def _write(self, msg: dict) -> None:
        """Write one JSON-RPC message to stdout, matching detected framing."""
        body = json.dumps(msg, ensure_ascii=False)
        logger.debug("→ %s", body)

        if self._ndjson is False:
            # LSP framing
            encoded = body.encode("utf-8")
            header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
            sys.stdout.buffer.write(header + encoded)
        else:
            # NDJSON (default when mode not yet determined)
            sys.stdout.buffer.write((body + "\n").encode("utf-8"))

        sys.stdout.buffer.flush()

    # ── Response helpers ──────────────────────────────────────────────────────

    def _ok(self, rid: Any, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    def _err(self, rid: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: Any) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_initialize(self, msg: dict) -> None:
        self._ok(msg["id"], {
            "serverInfo": {"name": "nanobanana", "version": "1.0.0"},
            "capabilities": {
                "promptCapabilities": {"image": True},
                "sessionCapabilities": {"named": True, "replace": True},
            },
        })

    def _on_session_new(self, msg: dict) -> None:
        params = msg.get("params") or {}
        # acpx passes --name as its own label; the agent receives sessionId/id/name
        # in params — or nothing at all. Fall back to a generated UUID.
        name = (params.get("sessionId")
                or params.get("id")
                or params.get("name")
                or str(uuid.uuid4()))
        logger.debug("session/new params=%s resolved name=%s", params, name)

        chat = self.gemini.create_chat()
        replaced = self.sessions.create(name, chat)
        self._ok(msg["id"], {"sessionId": name, "replaced": replaced})

    def _on_session_list(self, msg: dict) -> None:
        self._ok(msg["id"], {"sessions": self.sessions.list()})

    def _on_session_delete(self, msg: dict) -> None:
        params = msg.get("params") or {}
        name = params.get("name") or params.get("sessionId")
        deleted = self.sessions.delete(name) if name else False
        self._ok(msg["id"], {"deleted": deleted})

    def _on_session_prompt(self, msg: dict) -> None:
        params = msg.get("params") or {}
        rid = msg.get("id")
        logger.debug("session/prompt full params=%s", json.dumps(params, ensure_ascii=False))

        session_name = (params.get("sessionId")
                        or params.get("id")
                        or params.get("name"))
        files: list[str] = params.get("files") or []

        # acpx may send the prompt as:
        #   "text": "plain string"
        #   "text": [{"type": "text", "text": "..."}]   ← ACP content blocks
        #   "messages": [{"role": "user", "content": [...]}]
        raw = params.get("text") or params.get("prompt") or params.get("content")
        prompt = _extract_text(raw)
        if not prompt and "messages" in params:
            # Take the last user message
            for m in reversed(params["messages"]):
                t = _extract_text(m.get("content") or m.get("text") or "")
                if t:
                    prompt = t
                    break

        if not session_name:
            self._err(rid, -32602, "session/prompt 需要 sessionId 参数")
            return

        chat = self.sessions.get(session_name)
        if chat is None:
            self._err(rid, -32602, f"会话 [{session_name}] 不存在，请先用 session/new 创建")
            return
        if not prompt:
            self._err(rid, -32602, "session/prompt 需要 text 参数")
            return

        try:
            for chunk in self.gemini.send(chat, prompt, files):
                # Stream each content block as a session/update notification
                self._notify("session/update", {
                    "sessionId": session_name,
                    "requestId": rid,
                    "update": {"type": "content_block", "block": chunk},
                })
            # Signal completion
            self._notify("session/stopped", {
                "sessionId": session_name,
                "requestId": rid,
                "reason": "done",
            })
            self._ok(rid, {"done": True})
        except Exception as exc:
            logger.exception("session/prompt error")
            self._err(rid, -32603, _classify_error(exc))

    def _on_shutdown(self, msg: dict) -> None:
        self._shutdown = True
        self._ok(msg.get("id"), None)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        handlers = {
            "initialize":      self._on_initialize,
            "initialized":     lambda _: None,   # notification, no reply
            "session/new":     self._on_session_new,
            "session/list":    self._on_session_list,
            "session/delete":  self._on_session_delete,
            "session/prompt":  self._on_session_prompt,
            "shutdown":        self._on_shutdown,
            "exit":            lambda _: None,
        }

        while not self._shutdown:
            msg = self._read()
            if msg is None:
                break

            method = msg.get("method", "")
            handler = handlers.get(method)

            if handler:
                handler(msg)
            elif "id" in msg:
                # Unknown request — return an error
                self._err(msg["id"], -32601, f"未知方法: {method}")
            # Unknown notifications are silently ignored

        logger.info("nanobanana agent exiting")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(raw: Any) -> str:
    """Normalise an ACP text field that may be a plain string or a list of content blocks."""
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(block["text"])
        return " ".join(p for p in parts if p)
    return str(raw)


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "api key" in msg or "invalid key" in msg:
        return "Google API Key 无效，请检查配置"
    if "quota" in msg or "rate" in msg:
        return "API 配额不足，请稍后再试"
    if "safety" in msg or "blocked" in msg:
        return "生成内容不符合安全政策，请调整提示词"
    if "unsupported" in msg or "mime" in msg:
        return f"不支持的文件类型: {exc}"
    return str(exc)
