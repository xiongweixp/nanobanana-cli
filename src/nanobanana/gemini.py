"""Thin wrapper around the Google GenAI SDK."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Generator

from google import genai
from google.genai import types
from PIL import Image

logger = logging.getLogger(__name__)

SESSIONS_DIR = os.environ.get("NANOBANANA_SESSIONS_DIR", "/tmp/nanobanana")


class GeminiClient:
    """Manages the GenAI client and Gemini Chat session lifecycle."""

    def __init__(self) -> None:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GOOGLE_API_KEY 未设置，请先执行: export GOOGLE_API_KEY='...'"
            )
        self.client = genai.Client(api_key=api_key)
        self.default_model = os.environ.get(
            "NANOBANANA_DEFAULT_MODEL",
            "gemini-3.1-flash-image-preview",
        )

    # ── Chat lifecycle ────────────────────────────────────────────────────────

    def create_chat(self, session_id: str | None = None) -> Any:
        """Create a Gemini Chat, restoring history from disk if available.

        - session/new  → session_id=None  → always fresh
        - session/load → session_id=<uuid> → restore if history file exists
        """
        history = self._load_history(session_id) if session_id else []
        return self.client.chats.create(
            model=self.default_model,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
            history=history,
        )

    def save_history(self, chat: Any, session_id: str) -> None:
        """Persist chat history to disk so the next process can restore it."""
        raw = getattr(chat, "history", None) or getattr(chat, "_history", [])
        history = []
        for content in raw:
            parts = []
            for part in content.parts:
                if part.text:
                    parts.append({"text": part.text})
                elif part.inline_data and part.inline_data.data:
                    parts.append({
                        "inline_data": {
                            "mime_type": part.inline_data.mime_type or "image/png",
                            "data": base64.b64encode(part.inline_data.data).decode(),
                        }
                    })
            if parts:
                history.append({"role": content.role, "parts": parts})

        os.makedirs(SESSIONS_DIR, exist_ok=True)
        path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"history": history}, f)
        logger.debug("history saved → %s (%d turns)", path, len(history))

    def delete_history(self, session_id: str) -> None:
        path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if os.path.exists(path):
            os.unlink(path)

    # ── Messaging ─────────────────────────────────────────────────────────────

    def send(
        self,
        chat: Any,
        prompt: str,
        file_paths: list[str] | None = None,
    ) -> Generator[dict, None, None]:
        """Send a message and yield response chunks.

        Each chunk is one of:
          {"type": "text",  "text": "..."}
          {"type": "image", "data": "<base64>", "mime_type": "image/png"}
        """
        # chat.send_message() does NOT accept a bare list.
        # Multi-part content must be wrapped in types.Content.
        if file_paths:
            parts: list[types.Part] = []
            for path in file_paths:
                img = Image.open(path)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                parts.append(types.Part(
                    inline_data=types.Blob(mime_type="image/png", data=buf.getvalue())
                ))
            parts.append(types.Part(text=prompt))
            msg: Any = types.Content(role="user", parts=parts)
        else:
            msg = prompt

        response = chat.send_message(msg)

        for part in response.parts:
            if part.text:
                yield {"type": "text", "text": part.text}
            elif part.inline_data and part.inline_data.data:
                yield {
                    "type": "image",
                    "data": base64.b64encode(part.inline_data.data).decode(),
                    "mime_type": part.inline_data.mime_type or "image/png",
                }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_history(self, session_id: str) -> list[types.Content]:
        path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            history = []
            for msg in state.get("history", []):
                parts = []
                for p in msg.get("parts", []):
                    if "text" in p:
                        parts.append(types.Part(text=p["text"]))
                    elif "inline_data" in p:
                        parts.append(types.Part(
                            inline_data=types.Blob(
                                mime_type=p["inline_data"]["mime_type"],
                                data=base64.b64decode(p["inline_data"]["data"]),
                            )
                        ))
                if parts:
                    history.append(types.Content(role=msg["role"], parts=parts))
            logger.debug("history restored ← %s (%d turns)", path, len(history))
            return history
        except Exception as exc:
            logger.warning("failed to restore history %s: %s", path, exc)
            return []
