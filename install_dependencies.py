"""Install the Python packages required by the operator console.

Intended for Spyder users: run this file *from the same Spyder Python kernel*
that will run ``run_operator_console.py``.  This uses ``sys.executable`` so
packages are installed into that interpreter rather than an unrelated Python.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def main() -> None:
    root = Path(__file__).resolve().parent
    requirements = root / "requirements.txt"
    print(f"Python interpreter: {sys.executable}")
    print(f"Installing requirements from: {requirements}")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-r", str(requirements)
    ])
    print("\nInstallation complete.")
    print("If Spyder still reports an import error, restart the Spyder kernel and run run_operator_console.py again.")


if __name__ == "__main__":
    main()
