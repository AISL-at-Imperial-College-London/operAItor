"""Launch the Bokeh master operator console from Spyder or normal Python.

If Bokeh is missing, this launcher prints an exact installation command for
*the Python interpreter currently used by Spyder*.  Alternatively run
``install_dependencies.py`` once from Spyder.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import subprocess
import sys


def _missing_packages() -> list[str]:
    required = ("numpy", "scipy", "matplotlib", "bokeh")
    return [name for name in required if importlib.util.find_spec(name) is None]


def main() -> None:
    root = Path(__file__).resolve().parent
    app = root / "operator_console.py"
    requirements = root / "requirements.txt"

    missing = _missing_packages()
    if missing:
        quoted_python = f'"{sys.executable}"'
        quoted_req = f'"{requirements}"'
        print("\nOperator console cannot start because required packages are missing:")
        print("  " + ", ".join(missing))
        print(f"\nSpyder is currently using:\n  {sys.executable}")
        print("\nEASIEST FIX:")
        print("  Run install_dependencies.py once from Spyder, then restart the kernel.")
        print("\nOr run this command in a terminal/Anaconda Prompt using the SAME interpreter:")
        print(f"  {quoted_python} -m pip install -r {quoted_req}")
        print("\nIf only Bokeh is missing, Spyder's IPython console can also run:")
        print('  %pip install "bokeh>=3.4"')
        raise SystemExit(1)

    # Launch through ``python -m bokeh`` so we do not depend on Bokeh's
    # internal Python command API.  The server uses this package directory as
    # its working directory, so ``integrated_plant`` imports resolve normally.
    command = [
        sys.executable, "-m", "bokeh", "serve", "--show", str(app)
    ]
    print(f"Using Python: {sys.executable}")
    print("Starting operator console. Close the server with Ctrl+C in this console.")
    subprocess.run(command, cwd=str(root), check=False)


if __name__ == "__main__":
    main()
