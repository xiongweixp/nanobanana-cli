"""Entry point: `nanobanana` or `python -m nanobanana`."""

from __future__ import annotations

import json
import sys


def main() -> None:
    # GeminiClient raises EnvironmentError if GOOGLE_API_KEY is missing.
    # Catch it here so we can send a proper JSON-RPC error before exiting.
    try:
        from .server import NanobananaServer
        NanobananaServer().run()
    except EnvironmentError as exc:
        _fatal(str(exc))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _fatal(str(exc))
        sys.exit(1)


def _fatal(message: str) -> None:
    """Send a JSON-RPC notification to stderr and stdout so acpx can display it."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "agent/fatalError",
        "params": {"message": message},
    }, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()
    print(f"[nanobanana] fatal: {message}", file=sys.stderr)


if __name__ == "__main__":
    main()
