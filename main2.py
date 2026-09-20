#!/usr/bin/env python3
import json
import logging
import os
import sys
from pathlib import Path


def _run_local(root: Path) -> None:
    """Read request.txt and print the response for quick debugging."""
    sys.path.insert(0, str(root / "src"))
    from agent.brain2 import decide

    request_path = root / "request.txt"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    commands, prompt, execute_cmd = decide(payload)
    response = {
        "roleCommandMap": commands,
        "prompt": prompt,
        "executeCmd": execute_cmd,
    }
    print(json.dumps(response, ensure_ascii=False, indent=2))


def main() -> None:
    root = Path(__file__).resolve().parent
    os.chdir(root)
    sys.path.insert(0, str(root / "src"))

    if len(sys.argv) >= 2 and sys.argv[1] in ("--local", "-l", "local"):
        _run_local(root)
        return

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python main2.py <port> | python main2.py --local")
    port = int(sys.argv[1])

    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
    )

    from agent.server2 import serve

    logging.info("listening on 0.0.0.0:%d", port)
    serve(port)


if __name__ == "__main__":
    main()
