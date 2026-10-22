# PyInstaller spec for the OperAItor desktop app, on the system it runs on:
#   Windows  OperAItor\OperAItor.exe (+ _internal\); packaging/windows/installer.iss makes the installer
#   macOS    OperAItor.app (a windowed app bundle); .github/workflows/desktop.yml makes the .dmg
#   Linux    OperAItor/OperAItor (+ _internal/); packed as a .tar.gz
#
#   pip install "dist/operaitor-<version>-py3-none-any.whl[llm]" pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.8
#   (+ admit-bench for ADMIT; PyInstaller must be recent enough for the installed NumPy)
#   pyinstaller packaging/desktop/operaitor.spec --noconfirm
# The wheel must be built first (packaging/build_dist.py) so the web console is inside the package.
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

import operaitor

BRANDING = os.path.join(SPECPATH, "..", "..", "branding")
IS_MAC = sys.platform == "darwin"
ICON = os.path.join(BRANDING, "operaitor.icns" if IS_MAC else "operaitor.ico") if sys.platform != "linux" else None

datas, binaries, hiddenimports = [], [], []
for pkg in ("operaitor", "casadi", "langgraph", "langchain_core", "langchain_openai", "langchain_ollama", "tiktoken",
            "tiktoken_ext", "admitbench"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:          # optional package not installed (e.g. ADMIT)
        continue
    datas += d
    binaries += b
    hiddenimports += h
# uvicorn loads its event loop, HTTP and WebSocket implementations by name.
hiddenimports += collect_submodules("uvicorn") + collect_submodules("websockets") + ["httptools", "h11"]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OperAItor",
    # Windows / Linux: the window shows where it runs and closing it stops it. macOS: an app bundle without a
    # terminal; it stops with Quit OperAItor in the console.
    console=not IS_MAC,
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OperAItor")
if IS_MAC:
    app = BUNDLE(
        coll,
        name="OperAItor.app",
        icon=ICON,
        bundle_identifier="uk.ac.imperial.aisl.operaitor",
        version=operaitor.__version__,
        info_plist={
            "CFBundleName": "OperAItor",
            "CFBundleDisplayName": "OperAItor",
            "CFBundleShortVersionString": operaitor.__version__,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "OperAItor, AISL, Imperial College London. Apache License 2.0.",
        },
    )
