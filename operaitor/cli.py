"""Command line: ``operaitor <command>`` (or ``python -m operaitor <command>``).

    operaitor serve                         # console + agents on http://localhost:8001
    operaitor serve --llm off               # no API calls: agents monitor only, offline chat
    operaitor serve --agent llm_judge       # pick an agent configuration
    operaitor serve --hosted                # public deployment: visitors bring their own key
    operaitor doctor                        # check the installation
    operaitor train-pca                     # retrain the PCA fault-detection model
    operaitor benchmark probe|compare|report # 20-scenario fault campaign (see docs/benchmark.md)
    operaitor dashboard                     # build the campaign dashboard HTML
    operaitor admit build|validate|run      # the plant as an ADMIT cartridge (needs admit-bench)

LLM: OpenAI gpt-5-nano by default, when langchain-openai is installed and OPENAI_API_KEY is set
(environment or a .env file in the working directory). Other model names use a local Ollama, or
any OpenAI-compatible server via PLANT_LLM_BASE_URL. Without an LLM the agents only monitor.

Agent configurations: passive, llm (LLM + digital twin), llm_judge, llm_lucky, llm_admit,
llm_plan, llm_nokg, llm_notwin.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from . import __version__


def _serve(args: argparse.Namespace) -> None:
    import uvicorn
    from .agents.llm import DEFAULT_MODEL
    os.environ.update(PLANT_LLM=args.llm, PLANT_AGENT=args.agent, PLANT_LLM_MODEL=args.llm_model or DEFAULT_MODEL,
                      PLANT_CHAT_MODEL=args.chat_model, PLANT_DIAGNOSER=args.diagnoser)
    if args.hosted:
        os.environ.setdefault("PLANT_HOSTED", "1")
        os.environ.setdefault("PLANT_CAMPAIGN_DIR", "")  # nothing written to disk
    if args.max_sessions:
        os.environ["PLANT_MAX_SESSIONS"] = str(args.max_sessions)
    uvicorn.run("operaitor.server.app:app", host=args.host, port=args.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")


def _doctor(_: argparse.Namespace) -> int:
    ok = True

    def line(good: bool, what: str, hint: str = "") -> None:
        nonlocal ok
        ok &= good or not hint.startswith("!")
        print(f"  [{'ok' if good else '--'}] {what}" + ("" if good or not hint else f"   -> {hint.lstrip('!')}"))

    print(f"operaitor {__version__}, Python {sys.version.split()[0]}")
    for mod, extra, required in [("numpy", "", True), ("scipy", "", True), ("fastapi", "", True),
                                 ("uvicorn", "", True), ("langgraph", "", True),
                                 ("langchain_openai", "[llm]", False), ("dotenv", "[llm]", False),
                                 ("admitbench", "admit-bench", False), ("matplotlib", "[bench]", False)]:
        try:
            importlib.import_module(mod)
            line(True, mod)
        except ImportError:
            what = '"admit-bench @ git+https://github.com/Refiant-Inc/admit-bench.git"' if extra == "admit-bench" else f'"operaitor{extra}"'
            line(False, mod, ("!" if required else "") + f"pip install {what}")
    from .fdd import DEFAULT_MODEL_PATH
    line(os.path.exists(DEFAULT_MODEL_PATH), "PCA model", "!operaitor train-pca")
    from .agents.kg import KnowledgeGraph
    try:
        kg = KnowledgeGraph()
        line(True, f"knowledge graph ({len(kg.nodes)} nodes)")
    except Exception as exc:  # pragma: no cover - broken install
        line(False, f"knowledge graph: {exc}", "!reinstall the package")
    from .server.app import _frontend_dist
    dist = _frontend_dist()
    line((dist / "index.html").exists(), f"console build ({dist})", "cd frontend; npm install; npm run build")
    from .agents.llm import llm_status
    good, reason = llm_status()
    line(good, f"LLM: {reason}", "optional; agents monitor only without it")
    print("ready" if ok else "fix the items marked -> above")
    return 0 if ok else 1


def _passthrough(module: str):
    def run(args: argparse.Namespace) -> None:
        mod = importlib.import_module(module)
        sys.argv = [module, *args.rest]
        mod.main()
    return run


PASSTHROUGH = {"train-pca": "operaitor.fdd.train", "benchmark": "operaitor.benchmark.profit_campaign",
               "dashboard": "operaitor.benchmark.build_dashboard", "loop-tests": "operaitor.benchmark.loop_tests",
               "admit": "operaitor.admit.build"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in PASSTHROUGH:  # hand every remaining argument (including --options) to the tool
        mod = importlib.import_module(PASSTHROUGH[argv[0]])
        sys.argv = [f"operaitor {argv[0]}", *argv[1:]]
        out = mod.main()
        return out if isinstance(out, int) else 0
    ap = argparse.ArgumentParser(prog="operaitor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"operaitor {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run the web console and agents")
    s.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8001)))
    s.add_argument("--llm", choices=["auto", "on", "off"], default=os.environ.get("PLANT_LLM", "auto"))
    s.add_argument("--agent", default=os.environ.get("PLANT_AGENT", ""),
                   help="agent configuration (default: llm when an LLM is available, else passive)")
    s.add_argument("--llm-model", default=os.environ.get("PLANT_LLM_MODEL", ""), help="model for the agents")
    s.add_argument("--chat-model", default=os.environ.get("PLANT_CHAT_MODEL", ""), help="model for the chat agent")
    s.add_argument("--diagnoser", choices=["", "llm", "heuristic"], default=os.environ.get("PLANT_DIAGNOSER", ""))
    s.add_argument("--hosted", action="store_true", default=os.environ.get("PLANT_HOSTED") == "1",
                   help="public deployment: only visitor-supplied OpenAI keys, nothing written to disk")
    s.add_argument("--max-sessions", type=int, default=0, help="concurrent plant sessions (default 4)")
    s.set_defaults(func=_serve)

    d = sub.add_parser("doctor", help="check the installation")
    d.set_defaults(func=_doctor)

    for name, module, help_ in [("train-pca", "operaitor.fdd.train", "retrain the PCA fault-detection model"),
                                ("benchmark", "operaitor.benchmark.profit_campaign", "20-scenario fault campaign"),
                                ("dashboard", "operaitor.benchmark.build_dashboard", "build the campaign dashboard"),
                                ("loop-tests", "operaitor.benchmark.loop_tests", "controller setpoint tests"),
                                ("admit", "operaitor.admit.build", "ADMIT cartridge: build | validate | run")]:
        p = sub.add_parser(name, help=help_, add_help=False)
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=_passthrough(module))

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
