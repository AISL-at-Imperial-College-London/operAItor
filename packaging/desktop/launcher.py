"""OperAItor desktop app (Windows, macOS, Linux): start the console on this computer and open it in the browser.

Frozen by packaging/desktop/operaitor.spec (PyInstaller) into OperAItor.exe (Windows), OperAItor.app (macOS) or
the OperAItor program (Linux). It runs the same server as `operaitor serve`, on this computer only (127.0.0.1),
on port 8001 or the next free one, and stops with **Quit OperAItor** in the console's header, by closing the
window it runs in (Windows, Linux), or with Ctrl+C.

Its folder (data_dir) holds the reports (human tests, campaigns, pipeline benchmarks) and an optional .env with
OPENAI_API_KEY or PLANT_* settings for the agents:
    Windows  %LOCALAPPDATA%\\OperAItor
    macOS    ~/Library/Application Support/OperAItor
    Linux    $XDG_DATA_HOME/operaitor (~/.local/share/operaitor)
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


def data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OperAItor"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OperAItor"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "operaitor"


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


def _running_instance(preferred: int = 8001) -> str | None:
    """An OperAItor already running on this computer (e.g. the app opened twice): its address."""
    for port in range(preferred, preferred + 20):
        url = f"http://127.0.0.1:{port}/"
        try:
            with urllib.request.urlopen(url + "api/health", timeout=1) as r:
                if b'"ok"' in r.read(200):
                    return url
        except OSError:
            continue
    return None


def _open_when_ready(url: str) -> None:
    for _ in range(240):
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2):
                break
        except OSError:
            time.sleep(0.5)
    stop = "Use Quit OperAItor in the console to stop it." if sys.platform == "darwin" else \
        "Close this window (or use Quit OperAItor in the console) to stop it."
    print(f"\n  OperAItor is running at {url}\n  {stop}\n", flush=True)
    if os.environ.get("OPERAITOR_NO_BROWSER") != "1":
        webbrowser.open(url)


def main() -> None:
    data = data_dir()
    (data / "reports").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PLANT_CAMPAIGN_DIR", str(data / "reports"))
    os.environ.setdefault("PLANT_DOTENV", str(data / ".env"))
    os.environ["OPERAITOR_DESKTOP"] = "1"   # the console offers Quit OperAItor (server/app.py /api/quit)
    os.chdir(data)                         # nothing is written next to the program files
    if not os.environ.get("OPERAITOR_PORT"):
        running = _running_instance()
        if running:                        # opened again: show the console that is already running
            if os.environ.get("OPERAITOR_NO_BROWSER") != "1":
                webbrowser.open(running)
            print(f"OperAItor is already running at {running}", flush=True)
            return
    port = int(os.environ.get("OPERAITOR_PORT") or _free_port())
    url = f"http://127.0.0.1:{port}/"
    from operaitor import __version__
    print(f"OperAItor {__version__}: starting (the first start can take a minute)...", flush=True)
    threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    import uvicorn
    from operaitor.server.app import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def quit_app() -> None:
        server.should_exit = True
    app.state.quit = quit_app             # Quit OperAItor in the console
    server.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # keep the window open so the user can read what went wrong
        import traceback
        traceback.print_exc()
        print(f"\nOperAItor could not start: {exc}", file=sys.stderr)
        try:
            input("Press Enter to close.")
        except (EOFError, RuntimeError):   # no keyboard (macOS app, a script): just exit
            pass
        sys.exit(1)
