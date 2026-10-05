"""Automated fault injection: run a timed fault campaign against the live agents.

Campaigns (server/campaigns.py) are timed sequences of master injections; the
monitoring → KG → planning → action agents must detect, diagnose and respond to
each one. At the end a per-fault report (detection delay, top-1/top-3 diagnosis,
planning target, first action) is printed and saved under campaign_results/.

    py -3 fault_campaign.py list                               # presets + fault library
    py -3 fault_campaign.py run --campaign demo                 # against a running run_server.py (watch the console)
    py -3 fault_campaign.py run --campaign random --seed 3 --n 5
    py -3 fault_campaign.py run --campaign demo --headless      # no server: plant + agents in this process
    py -3 fault_campaign.py run --campaign demo --headless --agent rule_twin

Custom campaigns: pass a JSON file, e.g. my_campaign.json containing
    {"name": "mine", "items": [{"at_min": 5, "fault": "reflux_pump_trip", "duration_min": 10},
                               {"at_min": 25, "fault": "condenser_cooling_loss", "severity": 70}]}
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from agents.llm import DEFAULT_MODEL
from server.campaigns import CAMPAIGNS, FAULT_LIBRARY


def _campaign_arg(value: str):
    p = Path(value)
    return json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" and p.exists() else value


class Printer:
    """Compact live log: injections, fault transitions, agent decisions."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.last_fault: tuple | None = None

    def __call__(self, ev: dict) -> None:
        kind, t = ev.get("type"), ev.get("time_min", 0.0) or 0.0
        if kind == "monitor":
            sig = (ev["fault"], ev["diagnosis"]["summary"])
            if sig != self.last_fault or self.verbose:
                self.last_fault = sig
                pca = ev.get("pca") or {}
                state = f"FAULT → {sig[1]}" if sig[0] else "normal"
                print(f"{t:7.1f}  monitor   {state:45s} T²={pca.get('t2', 0):7.1f}/{pca.get('t2_limit', 0):.0f} "
                      f"SPE={pca.get('spe', 0):6.1f}/{pca.get('spe_limit', 0):.0f}", flush=True)
        elif kind == "agent":
            stage = ev["stage"]
            if stage == "plan":
                b = ev.get("brief", {})
                levers = ", ".join(f"{l['type']} {l.get('target', '')}".strip() for l in b.get("candidate_levers", [])[:4])
                print(f"{t:7.1f}  planning  target={b.get('target_fault')}  levers: {levers}", flush=True)
            elif stage == "reprompt":
                fb = ev.get("feedback") or {}
                print(f"{t:7.1f}  reprompt  retry {ev.get('attempt')}/{ev.get('reprompt_max')} "
                      f"after {fb.get('fail_reason')} → back to the action agent", flush=True)
            elif stage == "twin":
                verdict = "faulty variables back within limits" if ev.get("restored") else (
                    "keeps it safe, still faulty" if ev.get("safe_reached") else "does not keep it safe")
                print(f"{t:7.1f}  twin      {verdict} (terminal {ev.get('terminal_status')})", flush=True)
            elif stage == "validation":
                bad = [c for c in ev.get("checks", []) if not c["ok"]]
                print(f"{t:7.1f}  validate  {'ACCEPT' if ev.get('accepted') else 'REJECT'}"
                      + (f": {bad[0]['check']} ({bad[0]['detail']})" if bad else ""), flush=True)
            elif stage == "verification":
                print(f"{t:7.1f}  verify    {ev.get('reason')}", flush=True)
            elif stage == "fallback":
                print(f"{t:7.1f}  fallback  {ev.get('reason')}: {(ev.get('plan') or {}).get('commands')}", flush=True)
            elif stage in ("proposal", "applied", "pending", "advisory", "rejected", "invalid"):
                detail = (ev.get("plan") or {}).get("commands") if stage in ("proposal", "advisory") else \
                    ev.get("result", {}).get("summary") if stage == "applied" else \
                    (ev.get("accepted"), ev.get("fail_reason")) if stage == "twin" else \
                    ev.get("errors") or ev.get("reason") or ev.get("action")
                print(f"{t:7.1f}  {stage:9s} {json.dumps(detail, default=str)[:150]}", flush=True)
        elif kind == "message" and ev.get("level") in ("warn", "error"):
            print(f"{t:7.1f}  plant     {ev['text']}", flush=True)


def print_report(report: list[dict]) -> None:
    if not report:
        print("\nno injections were made")
        return
    print(f"\n{'fault':34s} {'detected':>8s} {'delay':>6s} {'top1':>5s} {'top3':>5s}  {'plan target':28s} first action")
    for r in report:
        delay = "" if r["detection_delay_min"] is None else f"{r['detection_delay_min']:.1f}"
        detected = "carry" if r.get("carry_over") else str(r["detected"])
        print(f"{r['fault']:34s} {detected:>8s} {delay:>6s} {str(r['top1_correct']):>5s} "
              f"{str(r['top3_correct']):>5s}  {str(r['plan_target']):28s} {json.dumps(r['first_action'])[:60]}")
    carried = [r for r in report if r.get("carry_over")]
    if carried:
        print(f"\ncarry: plant was already abnormal when {len(carried)} fault(s) were injected; "
              "detection/diagnosis not assessable for those (lengthen the gaps or reduce severities)")
    report = [r for r in report if not r.get("carry_over")]
    n = len(report) or 1
    det = [r for r in report if r["detected"]]
    print(f"\ndetected {len(det)}/{n} · top-1 {sum(r['top1_correct'] for r in report)}/{n} · "
          f"top-3 {sum(r['top3_correct'] for r in report)}/{n} · acted on {sum(r['first_action'] is not None for r in report)}/{n}")


def _start_msg(args) -> dict:
    msg = {"type": "campaign_start", "campaign": _campaign_arg(args.campaign), "scenario": args.scenario,
           "tail_min": args.tail}
    if args.campaign == "random":
        msg.update(seed=args.seed, n=args.n, gap_min=args.gap, duration_min=args.duration)
    return msg


async def run_remote(args) -> None:
    import websockets
    printer = Printer(args.verbose)
    async with websockets.connect(args.server, max_size=None) as ws:
        hello = json.loads(await ws.recv())
        agent = hello.get("agent")
        print(f"connected to {args.server}; agents: {agent.get('preset') + ' / ' + agent.get('mode') if agent else 'OFFLINE'}")
        await ws.send(json.dumps({"type": "speed", "delay_ms": args.delay_ms}))
        await ws.send(json.dumps(_start_msg(args)))
        while True:
            ev = json.loads(await ws.recv())
            if ev.get("type") == "ack" and ev.get("request") == "campaign_start" and not ev.get("ok"):
                sys.exit(f"campaign rejected: {ev.get('summary')}")
            printer(ev)
            camp = ev.get("campaign") if ev.get("type") == "campaign" else None
            if camp and camp["state"] != "running":
                print(f"\ncampaign {camp['state']}; report: {camp.get('report_path')}")
                report = json.loads(Path(camp["report_path"]).read_text(encoding="utf-8"))["report"]
                print_report(report)
                return


async def run_headless(args) -> None:
    from agents.llm import llm_status
    from agents.recovery_graph import make_config
    from agents.supervisor import AsyncSupervisor
    from server.plant_service import PlantService

    service = PlantService()
    await service.start()
    preset = args.agent
    if preset == "auto":
        ok, reason = llm_status(args.llm_model)
        preset = "llm" if ok else "rule_twin"
        if not ok:
            print(f"no LLM ({reason}); using the rule agent with twin + reprompter")
    sup = AsyncSupervisor(service, make_config(preset, llm_model=args.llm_model), mode="autonomous")
    sup.start()
    events = service.subscribe(maxsize=10000)
    printer = Printer(args.verbose)
    service.tick_delay_s = args.delay_ms / 1000
    ack = await service.handle_command(_start_msg(args))
    if not ack["ok"]:
        sys.exit(f"campaign rejected: {ack['summary']}")
    print(f"headless: agents {sup.cfg.name} / autonomous; {ack['summary']}")
    while True:
        ev = await events.get()
        printer(ev)
        if ev.get("type") == "campaign" and ev["campaign"]["state"] != "running":
            print(f"\ncampaign {ev['campaign']['state']}; report: {ev['campaign'].get('report_path')}")
            print_report(service.campaign_report())
            u = sup.info()["llm_usage"]
            if u["calls"]:
                print(f"LLM usage: {u['calls']} calls, {u['input_tokens']} in / {u['output_tokens']} out tokens, "
                      f"~${u['cost_usd']:.4f}")
            await service.stop()
            return


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show campaign presets and the fault library")
    p = sub.add_parser("run", help="run a campaign")
    p.add_argument("--campaign", default="demo", help=f"{sorted(CAMPAIGNS)}, 'random', or a .json file")
    p.add_argument("--scenario", default="nominal", help="base episode the faults are injected into")
    p.add_argument("--seed", type=int, default=0, help="random campaign seed")
    p.add_argument("--n", type=int, default=4, help="random campaign: number of faults")
    p.add_argument("--gap", type=float, default=25.0, help="random campaign: minutes between faults")
    p.add_argument("--duration", type=float, default=15.0, help="random campaign: fault duration [min], 0 = persistent")
    p.add_argument("--tail", type=float, default=30.0, help="minutes to keep running after the last fault ends")
    p.add_argument("--delay-ms", type=float, default=0.0, help="plant pacing (0 = as fast as possible)")
    p.add_argument("--server", default="ws://127.0.0.1:8001/ws")
    p.add_argument("--headless", action="store_true", help="run plant + agents in this process instead")
    p.add_argument("--agent", default="auto", help="headless only: agent preset (auto = llm if OpenAI is available, else rule)")
    p.add_argument("--llm-model", default=DEFAULT_MODEL, help="headless only: model for LLM presets")
    p.add_argument("--verbose", action="store_true", help="print every monitoring cycle")
    args = ap.parse_args()

    if args.cmd == "list":
        print("Campaign presets:")
        for name, c in CAMPAIGNS.items():
            items = ", ".join(f"{i['fault']}@{i['at_min']:g}" for i in c["items"])
            print(f"  {name:20s} {c['description']}\n  {'':20s} {items}")
        print("  random               seeded random sequence (--seed --n --gap --duration)")
        print("\nFault library:")
        for name, t in FAULT_LIBRARY.items():
            print(f"  {name:34s} {json.dumps({k: v for k, v in t.items() if k != 'vary'})}")
        return
    asyncio.run(run_headless(args) if args.headless else run_remote(args))


if __name__ == "__main__":
    main()
