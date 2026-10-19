import { type ReactNode, useEffect, useLayoutEffect, useState } from "react";
import { navigate, store } from "../store";
import type { Snapshot } from "../types";
import type { Drawer, Tab } from "./TopBar";

// The first-visit tour: a game-style walkthrough that points at one part of the console at a time and, at the steps
// that matter, waits for the visitor to do it (run the plant, open Faults, inject a fault, open Agents). It starts
// by itself on the first visit; the Tour button in the header replays it.

const SEEN_KEY = "operaitor.tour";
export const tourSeen = () => {
  try { return localStorage.getItem(SEEN_KEY) === "done"; } catch { return false; }
};
const markSeen = () => {
  try { localStorage.setItem(SEEN_KEY, "done"); } catch { /* storage unavailable: the tour shows again next visit */ }
};

interface Step {
  title: string;
  body: ReactNode;
  target?: string;                         // CSS selector of what to point at (none: a card in the middle)
  tab?: Tab;                               // shown on this tab
  drawer?: Drawer | "";                    // with this panel open ("" closes it)
  task?: { todo: string; done: (snap: Snapshot, since: number) => boolean };   // the visitor does this (since the step began)
}

const drawerOpen = (title: string) => !!document.querySelector(`aside.drawer[aria-label="${title}"]`);
// accepted by the server since the step began (a paused plant shows the fault only from its next step)
const injected = (_: Snapshot, since: number) => store.state.injectedAt > since;

export const STEPS: Step[] = [
  { title: "Welcome to the control room",
    body: <>OperAItor is a framework for AI operator agents. This is its demo plant, a simulated acetone process: a reactor
      (R-101) makes acetone from isopropanol, and a distillation column (T-201) purifies it. The agents supervise it,
      and you can too.<br />In two minutes you will run the plant, break it on
      purpose, and see who fixes it.</> },
  { title: "Plant status", target: ".header-status .status", tab: "plant",
    body: <>The one thing to watch. <b className="ok-text">Normal</b> is green; it turns amber for a fault or a safety
      function and red for a trip.</> },
  { title: "Start the clock", target: ".transport", tab: "plant",
    body: <>The plant is paused: time runs only when you say so. <b>+1 min</b> steps it by hand, and the speed menu goes from
      real time to hours in seconds.</>,
    task: { todo: "Press ▶ Run", done: (s) => s.running } },
  { title: "The plant, live", target: ".view-switch", tab: "plant",
    body: <>The process graphic shows every flow and level as it changes. Switch to <b>P&amp;ID</b> for the engineering
      drawing: instruments, safety interlocks and the cause &amp; effect matrix, lit up when a safety function trips.</> },
  { title: "Your controls", target: '.drawer-buttons button[title^="Change setpoints"]', drawer: "",
    body: <>As the operator you change set points, controller modes, pumps and emergency devices here. (A control loop on the
      Plant tab opens it too.)</>,
    task: { todo: "Open Operate", done: () => drawerOpen("Operate the plant") } },
  { title: "Break something", target: '.drawer-buttons button[title^="Inject faults"]',
    body: <>Faults test whoever is operating, agents or human. Nobody is told what you injected: they have to work it
      out from the measurements.</>,
    task: { todo: "Open Faults", done: () => drawerOpen("Inject faults") } },
  { title: "Inject a fault", target: "aside.drawer .btn.danger", drawer: "faults",
    body: <>A pump trip is ready to go. Press <b>Inject</b>, then watch the status, the alarms and the trends.</>,
    task: { todo: "Press Inject", done: injected } },
  { title: "Meet the agents", target: '.tabs [data-tab="agents"]', drawer: "",
    body: <>A pipeline of agents watches the plant: it detects the fault, diagnoses it, plans, proposes an action, tries it
      on a digital twin, validates it, and only then acts.</>,
    task: { todo: "Open the Agents tab", done: () => !!document.querySelector('.tabs [data-tab="agents"][aria-selected="true"]') } },
  { title: "Give them a model", target: ".card.settings", tab: "agents",
    body: <>Without a language model the agents only monitor and diagnose. Connect one to let them act: free options
      are Groq, Google Gemini, OpenRouter, or your own Ollama. Your key stays in this browser.</> },
  { title: "Watch them decide", target: ".pipeline-card", tab: "agents",
    body: <>Each decision moves through these steps, live. With <i>pause the plant</i> on, the plant waits while they think;
      <i> Approve</i> mode asks you before anything is done.</> },
  { title: "Make it yours", target: "#customise", tab: "agents",
    body: <>Bring your own prompts, knowledge graph or Python agents, run them on the same plant, and benchmark them against
      the reference pipeline.</> },
  { title: "Or operate it yourself", target: ".op-mode",
    body: <>Switch to <b>Human operator</b> to turn the agents off and take over. Operate → <i>Human test</i> scores you on
      the same faults the agents face.</> },
  { title: "You are ready", target: '.header button[title^="Report a bug"]',
    body: <>If the plant trips, <b>↻ Restart</b> or <b>New episode</b> starts again. Found a bug or have an idea?
      <b> Feedback</b> opens a GitHub issue, already filled in. Replay this tour from <b>?</b> in the header at any time.</> },
];

type Box = { top: number; left: number; width: number; height: number };

/** Where the target is on screen (re-measured every frame); null when it is not shown. */
function useTargetBox(selector: string | undefined, step: number): Box | null {
  const [box, setBox] = useState<Box | null>(null);
  useLayoutEffect(() => {
    if (!selector) { setBox(null); return; }
    let scrolled = false;
    const measure = () => {
      const el = document.querySelector(selector);
      if (!el) { setBox(null); return; }
      if (!scrolled) { el.scrollIntoView({ block: "nearest", inline: "nearest" }); scrolled = true; }
      const r = el.getBoundingClientRect();
      setBox((b) => (b && b.top === r.top && b.left === r.left && b.width === r.width && b.height === r.height
        ? b : { top: r.top, left: r.left, width: r.width, height: r.height }));
    };
    // every frame: the card follows the layout at once when the page shifts (a header that wraps, a panel opening)
    let frame = 0;
    const loop = () => { measure(); frame = requestAnimationFrame(loop); };
    loop();
    return () => cancelAnimationFrame(frame);
  }, [selector, step]);
  return box;
}

export function Tour({ onClose }: { onClose: () => void }) {
  const [i, setI] = useState(0);
  const [doneStep, setDoneStep] = useState(-1);   // the step whose task was done: latched, so it cannot flicker back
  const step = STEPS[i];
  const box = useTargetBox(step.target, i);
  const last = i === STEPS.length - 1;
  const done = doneStep === i;

  useEffect(() => {   // put the console where the step is
    if (step.tab !== undefined || step.drawer !== undefined) navigate({ tab: step.tab, drawer: step.drawer });
  }, [i]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {   // watch for the task (some live in the page: an open panel, a tab), then move on by itself
    const task = step.task;
    if (!task) return;
    const since = Date.now();
    let next: ReturnType<typeof setTimeout> | undefined;
    const look = setInterval(() => {
      const s = store.state.snap;
      if (!s || !task.done(s, since)) return;
      clearInterval(look);
      setDoneStep(i);
      next = setTimeout(() => setI((n) => (n === i ? Math.min(n + 1, STEPS.length - 1) : n)), 900);   // a moment to see ✓
    }, 300);
    return () => { clearInterval(look); clearTimeout(next); };
  }, [i]);   // eslint-disable-line react-hooks/exhaustive-deps

  const finish = () => { markSeen(); onClose(); };
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") finish(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });   // eslint-disable-line react-hooks/exhaustive-deps

  const pad = 6;
  const hole = box && { top: box.top - pad, left: box.left - pad, width: box.width + 2 * pad, height: box.height + 2 * pad };
  // The card sits below the target, or above it when there is no room; in the middle when nothing is pointed at.
  const W = Math.min(360, window.innerWidth - 32);
  const below = hole && hole.top + hole.height + 220 < window.innerHeight;
  const cardStyle = hole
    ? { width: W, left: Math.max(16, Math.min(hole.left, window.innerWidth - W - 16)),
        ...(below ? { top: hole.top + hole.height + 12 } : { bottom: Math.max(16, window.innerHeight - hole.top + 12) }) }
    : { width: W, left: (window.innerWidth - W) / 2, top: Math.max(16, window.innerHeight * 0.28) };

  return (
    <div className="tour" role="dialog" aria-modal="false" aria-labelledby="tour-title">
      {hole ? <div className="tour-hole" style={hole} /> : <div className="tour-dim" />}
      <div className="tour-card" style={cardStyle}>
        <div className="tour-progress" aria-label={`Step ${i + 1} of ${STEPS.length}`}>
          {STEPS.map((_, n) => <span key={n} className={n < i ? "past" : n === i ? "now" : ""} />)}
        </div>
        <h3 id="tour-title">{step.title}</h3>
        <p>{step.body}</p>
        {step.task && (
          <p className={`tour-task ${done ? "done" : ""}`}>{done ? "✓ Done" : <>Your turn: <b>{step.task.todo}</b></>}</p>
        )}
        <div className="tour-buttons">
          {i === 0
            ? <button className="btn ghost sm" onClick={finish}>Skip the tour</button>
            : <button className="btn ghost sm" onClick={finish}>End tour</button>}
          <span className="spacer" />
          {i > 0 && <button className="btn sm" onClick={() => setI(i - 1)}>Back</button>}
          {last ? <button className="btn primary sm" onClick={finish}>Start playing</button>
            : <button className={`btn sm ${step.task && !done ? "" : "primary"}`} onClick={() => setI(i + 1)}>
                {i === 0 ? "Show me around" : step.task && !done ? "Skip this step" : "Next"}</button>}
        </div>
      </div>
    </div>
  );
}
