"""Smoke test of a built OperAItor desktop app (CI, every platform): python smoke_test.py <program> [args...]

(SMOKE_PORT sets the port, default 8012; e.g. `python smoke_test.py python packaging/desktop/launcher.py` tests
the launcher unpackaged.)

Starts the program as a user would (no browser), then checks that
  1. the server answers /api/health and serves the console page,
  2. a second start finds the running app and exits instead of starting another server,
  3. a page from another website cannot open the WebSocket (origin check), the console's own page can,
  4. the plant runs on the CasADi solver (bundled with its plugin libraries) and steps forward,
  5. the console reports the desktop app and "Quit OperAItor" stops the program cleanly.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

import websockets


def wait_health(url: str, proc: subprocess.Popen, seconds: float = 240) -> dict:
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            raise SystemExit(f"the app exited during start-up (code {proc.returncode})")
        try:
            with urllib.request.urlopen(url + "api/health", timeout=3) as r:
                return json.loads(r.read())
        except OSError:
            time.sleep(2)
    raise SystemExit(f"no answer from {url} after {seconds:.0f} s")


async def websocket_checks(port: int) -> None:
    uri = f"ws://127.0.0.1:{port}/ws"
    try:
        async with websockets.connect(uri, origin="https://example.com") as ws:
            await ws.recv()
        raise SystemExit("a page from another website could open the WebSocket")
    except (websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosed):
        pass  # refused, as it must be
    async with websockets.connect(uri, origin=f"http://127.0.0.1:{port}", max_size=None) as ws:
        hello = json.loads(await ws.recv())
        assert hello["type"] == "hello", hello["type"]
        assert hello["session"]["desktop"] is True, "the console does not know it is the desktop app"
        assert hello["session"]["plugins"]["allowed"] is True, "Python agents should run in the desktop app"
        await ws.send(json.dumps({"type": "step", "minutes": 1}))   # the plant steps (CasADi's Newton plugin loads)
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "ack" and msg.get("request") == "step":
                assert msg["ok"], msg
            if msg.get("type") == "snapshot" and msg.get("time_min", 0) >= 1.0 - 1e-6:
                assert not msg.get("done"), "the plant stopped during a nominal minute"
                break
        await ws.send(json.dumps({"type": "quit_app"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "ack" and msg.get("request") == "quit_app":
                assert msg["ok"], msg
                return


def main() -> None:
    program = sys.argv[1:]
    port = int(os.environ.get("SMOKE_PORT", 8012))
    env = {**os.environ, "OPERAITOR_NO_BROWSER": "1", "OPERAITOR_PORT": str(port)}
    url = f"http://127.0.0.1:{port}/"
    proc = subprocess.Popen(program, env=env)
    try:
        health = wait_health(url, proc)
        print("1. started:", health)
        assert health.get("solver") == "casadi", f"the app should run the plant on CasADi, not {health.get('solver')}"
        with urllib.request.urlopen(url, timeout=10) as r:
            assert b'id="root"' in r.read(), "the console page is missing"
        print("   console page served")

        second_env = {k: v for k, v in env.items() if k != "OPERAITOR_PORT"}
        second = subprocess.run(program, env=second_env, timeout=120, capture_output=True, text=True)
        assert second.returncode == 0 and "already running" in second.stdout, (second.returncode, second.stdout[-500:],
                                                                                 second.stderr[-500:])
        print("2. a second start opens the running app:", second.stdout.strip().splitlines()[-1])

        asyncio.run(websocket_checks(port))
        print("3. other websites are refused; the console connects; the plant steps on CasADi")
        code = proc.wait(timeout=60)
        assert code == 0, f"exit code {code} after Quit"
        print("4. Quit OperAItor stopped the app (exit code 0)")
    finally:
        if proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    main()
