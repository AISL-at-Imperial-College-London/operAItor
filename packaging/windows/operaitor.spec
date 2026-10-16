# PyInstaller spec for the OperAItor Windows app (one folder: OperAItor\OperAItor.exe + _internal\).
#   pip install "dist/operaitor-<version>-py3-none-any.whl[llm]" pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.8
#   (+ admit-bench for ADMIT; PyInstaller must be recent enough for the installed NumPy)
#   pyinstaller packaging/windows/operaitor.spec --noconfirm
# The wheel must be built first (packaging/build_dist.py) so the web console is inside the package.
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ICON = os.path.join(SPECPATH, "..", "..", "branding", "operaitor.ico")

datas, binaries, hiddenimports = [], [], []
for pkg in ("operaitor", "langgraph", "langchain_core", "langchain_openai", "langchain_ollama", "tiktoken",
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
    console=True,              # the window shows where it runs; closing it stops the server
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OperAItor")
