---
title: Acetone Plant Console
emoji: 🏭
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Simulated chemical plant with fault detection and LLM agents
---

# Acetone plant console

A simulated isopropanol → acetone plant: a reactor (CSTR) feeding a 10-tray distillation
column, with 8 PI control loops, safety functions (SIFs), alarms, PCA fault detection and LLM
recovery agents.

**Try it:**
1. Press **Run** to start the plant.
2. Open **Faults** and inject one, e.g. trip the reflux pump P-201A.
3. Watch the alarms, the fault detection, and the plant trip if nobody acts.

**Turn on the agents:** paste your own OpenAI API key in the **Assistant** panel.
- The key stays in server memory for your session only and is never stored.
- A run costs a few cents with gpt-5-nano.
- Without a key the agents monitor and diagnose, but take no actions.

Every visitor gets their own plant. Sessions end 15 minutes after the tab is closed.

Source and documentation: https://github.com/AISL-at-Imperial-College-London/acetone_reaction
