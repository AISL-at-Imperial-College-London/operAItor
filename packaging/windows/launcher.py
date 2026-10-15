"""OperAItor desktop app (Windows): start the console on this computer and open it in the browser.

Frozen into OperAItor.exe by packaging/windows/operaitor.spec (PyInstaller). It runs the same server as
`operaitor serve`, on this computer only (127.0.0.1), on port 8001 or the next free one. Closing the
window stops it. Reports (human tests, campaigns) go to %LOCALAPPDATA%\\OperAItor\\reports. An optional
%LOCALAPPDATA%\\OperAItor\\.env can hold OPENAI_API_KEY or PLANT_* settings for the agents.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _free_port(preferred: int = 8001) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:   # anything free
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _open_when_ready(url: str) -> None:
    for _ in range(240):
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2):
                break
        except OSError:
            time.sleep(0.5)
    print(f"\n  OperAItor is running at {url}\n  Close this window to stop it.\n", flush=True)
    if os.environ.get("OPERAITOR_NO_BROWSER") != "1":
        webbrowser.open(url)


def main() -> None:
    data = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OperAItor"
    (data / "reports").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PLANT_CAMPAIGN_DIR", str(data / "reports"))
    os.environ.setdefault("PLANT_DOTENV", str(data / ".env"))
    os.chdir(data)                       # nothing is written next to the program files
    port = int(os.environ.get("OPERAITOR_PORT") or _free_port())
    url = f"http://127.0.0.1:{port}/"
    from operaitor import __version__
    print(f"OperAItor {__version__}: starting (the first start can take a minute)...", flush=True)
    threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    import uvicorn
    from operaitor.server.app import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # keep the window open so the user can read what went wrong
        import traceback
        traceback.print_exc()
        print(f"\nOperAItor could not start: {exc}", file=sys.stderr)
        try:
            input("Press Enter to close.")
        except EOFError:       # no keyboard (started from a script): just exit
            pass
        sys.exit(1)
