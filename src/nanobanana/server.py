"""
ACP JSON-RPC 2.0 server over stdio.

Framing: LSP-style Content-Length headers
  Content-Length: <byte-length>\r\n
  \r\n
  <utf-8 json body>

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

    # ── Wire I/O ──────────────────────────────────────────────────────────────

    def _read(self) -> dict | None:
        """Read one JSON-RPC message from stdin (Content-Length framed)."""
        headers: dict[str, str] = {}
        while True:
            raw = sys.stdin.buffer.readline()
            if not raw:
                return None  # EOF
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                break  # blank line → end of headers
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()

        length = int(headers.get("Content-Length", 0))
        if length == 0:
            return None

        body = sys.stdin.buffer.read(length)
        msg = json.loads(body.decode("utf-8"))
        logger.debug("← %s", json.dumps(msg, ensure_ascii=False))
        return msg

    def _write(self, msg: dict) -> None:
        """Write one JSON-RPC message to stdout (Content-Length framed)."""
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        sys.stdout.buffer.write(header + body)
        sys.stdout.buffer.flush()
        logger.debug("→ %s", json.dumps(msg, ensure_ascii=False))

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
        name = params.get("name") or params.get("sessionId")
        if not name:
            self._err(msg["id"], -32602, "session/new 需要 name 参数")
            return

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
        session_name = params.get("sessionId") or params.get("name")
        prompt = params.get("text") or params.get("prompt", "")
        files: list[str] = params.get("files") or []

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
