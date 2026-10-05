"""Start the plant + agents backend and serve the React console.

    py -3 run_server.py                        # http://localhost:8001
    py -3 run_server.py --llm off              # no API calls: rule agents + offline chat
    py -3 run_server.py --agent rule_twin       # pick an agent preset explicitly
    py -3 run_server.py --llm-model gpt-5-mini  # a stronger (pricier) OpenAI model
    py -3 run_server.py --port 9000             # if 8001 is taken

LLM (default --llm auto): OpenAI gpt-5-nano, the cheapest OpenAI model, for the
planning/action agents (preset "llm") and the chat agent, whenever
langchain-openai is installed and OPENAI_API_KEY is set (environment or a .env
file in the repo root). Otherwise the server falls back to rule agents and the
offline chat router and says why. Non-"gpt"/"o" model names use a local Ollama.

Agent presets: passive, rule, rule_twin, pca_only, llm, llm_plan, llm_nokg, llm_notwin.

Build the frontend once with: cd frontend; npm install; npm run build
For frontend development run npm run dev instead (http://localhost:5173).
"""
import argparse
import os

import uvicorn

from agents.llm import DEFAULT_MODEL


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--llm", choices=["auto", "on", "off"], default="auto",
                    help="auto: use OpenAI if a key + langchain-openai are available")
    ap.add_argument("--agent", default="", help="recovery-graph preset (default: llm when the LLM is on, else rule_twin)")
    ap.add_argument("--llm-model", default=DEFAULT_MODEL, help="model for the planning/action agents")
    ap.add_argument("--chat-model", default="", help="model for the chat agent (default: same as --llm-model)")
    args = ap.parse_args()
    os.environ.update(PLANT_LLM=args.llm, PLANT_AGENT=args.agent, PLANT_LLM_MODEL=args.llm_model,
                      PLANT_CHAT_MODEL=args.chat_model)
    uvicorn.run("server.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
