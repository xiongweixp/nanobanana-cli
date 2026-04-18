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
        # Build content list: images first (if any), then the text prompt
        contents: list[Any] = []
        for path in file_paths or []:
            contents.append(Image.open(path))
        contents.append(prompt)

        # Single item → unwrap from list (SDK accepts both forms)
        msg = contents if len(contents) > 1 else contents[0]
        response = chat.send_message(msg)

        for part in response.parts:
            if part.text:
                yield {"type": "text", "text": part.text}
            else:
                img = part.as_image()
                if img is not None:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    yield {
                        "type": "image",
                        "data": base64.b64encode(buf.getvalue()).decode(),
                        "mime_type": "image/png",
                    }
