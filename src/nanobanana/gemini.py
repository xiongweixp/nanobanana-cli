"""Thin wrapper around the Google GenAI SDK."""

from __future__ import annotations

import base64
import io
import os
from typing import Any, Generator

from google import genai
from google.genai import types
from PIL import Image


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

    def create_chat(self) -> Any:
        """Create a new Gemini Chat instance (one per session)."""
        return self.client.chats.create(
            model=self.default_model,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

    def send(
        self,
        chat: Any,
        prompt: str,
        file_paths: list[str] | None = None,
    ) -> Generator[dict, None, None]:
        """
        Send a message to an existing chat and yield response chunks.

        Each chunk is one of:
          {"type": "text",  "text": "..."}
          {"type": "image", "data": "<base64>", "mime_type": "image/png"}
        """
        # chat.send_message() only accepts a single str / Part / Content —
        # NOT a bare list.  For multi-part payloads (text + images) we wrap
        # everything in a types.Content object.
        if file_paths:
            parts: list[types.Part] = []
            for path in file_paths:
                img = Image.open(path)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                parts.append(types.Part(
                    inline_data=types.Blob(
                        mime_type="image/png",
                        data=buf.getvalue(),
                    )
                ))
            parts.append(types.Part(text=prompt))
            msg: Any = types.Content(role="user", parts=parts)
        else:
            msg = prompt  # plain string — cheapest path

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
