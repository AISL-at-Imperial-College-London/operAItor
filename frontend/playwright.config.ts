// End-to-end tests of the console against a real OperAItor server (no LLM, no network).
//   npm run build && npx playwright test                    (CI: Chromium from `npx playwright install chromium`)
//   PW_CHANNEL=chrome OPERAITOR_PY="py -3" npx playwright test   (locally, with the installed Chrome)
// The server serves the built console (frontend/dist); delete operaitor/server/web first if it exists.
import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 8040);
const PY = process.env.OPERAITOR_PY ?? "python";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: process.env.PW_CHANNEL || undefined,
    trace: "retain-on-failure",
    viewport: { width: 1400, height: 1000 },
  },
  webServer: {
    command: `${PY} -m operaitor serve --port ${PORT} --llm off`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    timeout: 120_000,
    reuseExistingServer: false,
    // PLANT_BENCH_TICK_DELAY_S: pace benchmark runs (0.05 s per 0.5 sim-min), so a scenario can be watched and stopped
    env: { PLANT_MAX_SESSIONS: "50", PLANT_HOSTED: "0", PLANT_CAMPAIGN_DIR: "", PYTHONIOENCODING: "utf-8",
           PLANT_BENCH_TICK_DELAY_S: "0.05" },
  },
});
