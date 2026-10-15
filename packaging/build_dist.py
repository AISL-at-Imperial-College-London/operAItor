"""Build the release packages: the pip wheel + source archive, with the web console bundled inside.

    python packaging/build_dist.py            # -> dist/operaitor-<version>-py3-none-any.whl and .tar.gz

Steps: build the console (frontend/, npm), copy it into operaitor/server/web (where the server looks
for a bundled console), then build with `python -m build`. Needs Node 20+ and the `build` package
(pip install build). The Docker image and the Windows app are built from the same sources
(Dockerfile, packaging/windows/); .github/workflows/release.yml runs all three on a version tag.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "operaitor" / "server" / "web"


def run(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, shell=sys.platform == "win32" and cmd[0] in ("npm", "npx"))


def build_console() -> None:
    front = ROOT / "frontend"
    run(["npm", "ci", "--no-audit", "--no-fund"], front)
    run(["npm", "run", "build"], front)
    if WEB.exists():
        shutil.rmtree(WEB)
    shutil.copytree(front / "dist", WEB)
    if not (WEB / "index.html").exists():
        sys.exit("console build has no index.html")
    print(f"console bundled into {WEB.relative_to(ROOT)}")


def main() -> None:
    build_console()
    out = ROOT / "dist"
    if out.exists():
        shutil.rmtree(out)
    run([sys.executable, "-m", "build", "--outdir", str(out)], ROOT)
    for f in sorted(out.iterdir()):
        print(f"built {f.name} ({f.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
