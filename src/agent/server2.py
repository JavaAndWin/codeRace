import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .brain2 import decide

LOGGER = logging.getLogger(__name__)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
            commands, prompt, execute_cmd = decide(payload)
            LOGGER.info("round %s -> commands=%s prompt=%s",
                        payload.get("roundNo"), commands, prompt[:80] if prompt else "")
            body = json.dumps(
                {
                    "roleCommandMap": commands,
                    "prompt": prompt or "",
                    "executeCmd": execute_cmd or "",
                },
                ensure_ascii=False,
            ).encode("utf-8")
        except Exception:
            LOGGER.exception("decision failed")
            body = b'{"roleCommandMap":{},"prompt":"","executeCmd":""}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(port: int) -> None:
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
