from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .pipeline import PipelineConfig, match_source_part


WEB_ROOT = Path(__file__).with_name("web_assets")
MAX_REQUEST_BYTES = 64 * 1024
Matcher = Callable[..., Mapping[str, Any]]


def validate_source_part_info(source: Any) -> Mapping[str, Any]:
    if not isinstance(source, Mapping):
        raise ValueError("source_part_info must be a JSON object")
    vehicle = source.get("vehicle")
    if not isinstance(vehicle, Mapping):
        raise ValueError("vehicle must be a JSON object")
    required = {
        "year": vehicle.get("year"),
        "make": vehicle.get("make"),
        "model_guess": vehicle.get("model_guess"),
        "part_description": source.get("part_description"),
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    return source


def create_server(*, host: str = "127.0.0.1", port: int = 8000, matcher: Matcher = match_source_part) -> ThreadingHTTPServer:
    """Create the local Part Matcher web server."""

    allowed_hosts = {host.casefold()}
    if host.casefold() in {"127.0.0.1", "localhost", "::1"}:
        allowed_hosts.update({"127.0.0.1", "localhost", "::1"})

    class PartMatcherHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            body = (WEB_ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/api/match":
                self.send_error(404)
                return
            request_host = urlsplit(f"//{self.headers.get('Host', '')}").hostname
            if not request_host or request_host.casefold() not in allowed_hosts:
                self._send_json({"error": "Host is not allowed"}, status=403)
                return
            content_type = self.headers.get_content_type()
            if content_type != "application/json":
                self._send_json({"error": "Content-Type must be application/json"}, status=415)
                return
            origin = self.headers.get("Origin")
            expected_origin = f"http://{self.headers.get('Host', '')}"
            if origin and origin != expected_origin:
                self._send_json({"error": "Cross-origin requests are not allowed"}, status=403)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json({"error": "Invalid Content-Length"}, status=400)
                return
            if content_length < 0 or content_length > MAX_REQUEST_BYTES:
                self._send_json({"error": f"Request body exceeds {MAX_REQUEST_BYTES} bytes"}, status=413)
                return
            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise ValueError("Request body must be a JSON object")
                source = validate_source_part_info(payload.get("source_part_info"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            try:
                result = matcher(source, config=PipelineConfig(use_llm=payload.get("use_llm") is True))
            except Exception as exc:
                self._send_json({"error": str(exc) or exc.__class__.__name__}, status=502)
                return
            self._send_json(result)

        def _send_json(self, payload: Mapping[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), PartMatcherHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Conneverse Part Matcher web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="Open the UI in the default browser")
    args = parser.parse_args()
    server = create_server(host=args.host, port=args.port)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Part Matcher running at {url}")
    print("Press Ctrl+C to stop.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
