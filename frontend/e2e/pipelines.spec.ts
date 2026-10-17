// Agent pipelines in the console: baselines, customising, knowledge-graph upload, running, export / import.
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const KG = fileURLToPath(new URL("../../operaitor/data/knowledge_graph/Integrated_CSTR_Distillation_Knowledge_Graph.jsonld", import.meta.url));

async function openPipelines(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator(".tabs")).toBeVisible();
  await page.locator(".tabs button", { hasText: "Agents" }).click();
  const section = page.locator("details.section", { has: page.locator("summary", { hasText: "Agent pipeline" }) });
  await section.locator(":scope > summary").click();
  await expect(section.locator(".pl-list").first()).toBeVisible();
  return { section, errors };
}

const setting = (page: Page, label: string) => page.locator(".pl-setting", { hasText: label });

test("the console loads with the OperAItor logo", async ({ page }) => {
  await page.goto("/");
  const logo = page.locator("img.logo").first();
  await expect(logo).toBeVisible();
  await expect.poll(() => logo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBeGreaterThan(0);
  const icon = await page.request.get("/favicon.svg");
  expect(icon.ok()).toBeTruthy();
  expect(await icon.text()).toContain("control valve");
});

test("baselines are listed and one runs by default", async ({ page }) => {
  const { section, errors } = await openPipelines(page);
  const baselines = section.locator(".pl-list").first().locator("li");
  await expect(baselines).toHaveCount(5);
  await expect(baselines.locator(".pill", { hasText: "baseline" })).toHaveCount(5);
  await expect(section.locator(".pl-list li.on .pill", { hasText: "running" })).toHaveCount(1);
  await expect(section).toContainText("None yet");
  expect(errors).toEqual([]);
});

test("customise a baseline, upload a knowledge graph, save and run it", async ({ page }) => {
  const { section, errors } = await openPipelines(page);
  await section.locator(".pl-list li", { hasText: "LLM + digital twin" }).getByRole("button", { name: "Customise" }).click();
  const editor = section.locator(".pl-editor");
  await expect(editor).toBeVisible();
  await editor.getByLabel("Name").fill("e2e judge pipeline");

  // settings: changed values are highlighted and can be reset to the baseline
  await setting(page, "Check each action with").locator("select").selectOption("judge");
  await setting(page, "Retries after a rejected proposal").locator("input").fill("1");
  await expect(editor.locator(".pl-setting.changed")).toHaveCount(2);
  await expect(setting(page, "Check each action with")).toContainText("baseline: Digital twin");
  await setting(page, "Retries after a rejected proposal").getByRole("link", { name: "reset" }).click();
  await expect(editor.locator(".pl-setting.changed")).toHaveCount(1);

  // a prompt for the action agent; the baseline prompt is shown for reference
  await editor.getByRole("tab", { name: /Action agent/ }).click();
  await editor.locator("textarea.pl-prompt").fill("Prefer setpoint changes over switching pumps.");
  await expect(editor.getByRole("tab", { name: /Action agent ●/ })).toBeVisible();
  await editor.locator("summary", { hasText: "Baseline action agent prompt" }).click();
  await expect(editor.locator(".pl-ref-prompt")).toContainText("JSON");

  // the knowledge graph is checked on upload
  await editor.locator("input[type=file]").setInputFiles(KG);
  await expect(editor.locator(".kg-report")).toContainText("No problems found");
  await expect(editor.locator(".kg-report")).toContainText("16 of 16 reference faults covered");

  await editor.getByRole("button", { name: "Save and run" }).click();
  const mine = section.locator(".pl-list").nth(1).locator("li");
  await expect(mine).toHaveCount(1);
  await expect(mine).toContainText("e2e judge pipeline");
  await expect(mine).toContainText("1 setting · prompts: action · own knowledge graph");
  await expect(mine.locator(".pill", { hasText: /running · [0-9a-f]{10}/ })).toBeVisible();   // the server runs it
  await expect(section.locator(".pl-list").first().locator("li.on")).toHaveCount(0);
  await expect(page.locator(".settings-grid")).toContainText('your pipeline "e2e judge pipeline"');

  // kept in the browser and still running after a reload (same session)
  await page.reload();
  const again = await openPipelines(page);
  await expect(again.section.locator(".pl-list").nth(1).locator("li")).toContainText("e2e judge pipeline");
  await expect(again.section.locator(".pl-list li.on")).toContainText("e2e judge pipeline");

  // back to a baseline
  await again.section.locator(".pl-list li", { hasText: "LLM + LLM judge" }).getByRole("button", { name: "Run" }).click();
  await expect(again.section.locator(".pl-list").first().locator("li.on")).toContainText("LLM + LLM judge");
  await expect(again.section.locator(".pl-list").nth(1).locator("li.on")).toHaveCount(0);
  expect([...errors, ...again.errors]).toEqual([]);
});

test("a broken knowledge graph is refused with a reason", async ({ page }) => {
  const { section } = await openPipelines(page);
  await section.locator(".pl-list li", { hasText: "LLM + ADMIT gates" }).getByRole("button", { name: "Customise" }).click();
  await section.locator(".pl-editor input[type=file]").setInputFiles({ name: "broken.jsonld", mimeType: "application/json", buffer: Buffer.from("{not json") });
  await expect(section.locator(".pl-editor")).toContainText("not valid JSON");
});

test("export a pipeline and import it again", async ({ page }) => {
  const { section } = await openPipelines(page);
  await section.locator(".pl-list li", { hasText: "LLM, no validation" }).getByRole("button", { name: "Customise" }).click();
  await section.locator(".pl-editor").getByLabel("Name").fill("shared pipeline");
  await setting(page, "Planner").locator("select").selectOption("llm");
  await section.locator(".pl-editor").getByRole("button", { name: "Save", exact: true }).click();

  const [download] = await Promise.all([page.waitForEvent("download"),
    section.locator(".pl-list li", { hasText: "shared pipeline" }).getByRole("button", { name: "Export" }).click()]);
  expect(download.suggestedFilename()).toBe("shared_pipeline.operaitor-pipeline.json");
  const file = JSON.parse(readFileSync(await download.path(), "utf-8"));
  expect(file).toMatchObject({ operaitor_pipeline: 1, name: "shared pipeline", base: "llm_lucky", settings: { planner: "llm" } });

  await section.locator("input[type=file]").setInputFiles({ name: "x.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({ ...file, name: "imported pipeline" })) });
  await expect(section.locator(".pl-list").nth(1).locator("li")).toHaveCount(2);
  await expect(section.locator(".pl-list").nth(1)).toContainText("imported pipeline");

  await section.locator("input[type=file]").setInputFiles({ name: "bad.json", mimeType: "application/json", buffer: Buffer.from('{"base": "nope"}') });
  await expect(page.locator(".toast")).toContainText("not an OperAItor pipeline file");
});

test("write Python agents, check them, and they run on the live plant", async ({ page }) => {
  const { section, errors } = await openPipelines(page);
  await section.locator(".pl-list li", { hasText: "LLM + digital twin" }).getByRole("button", { name: "Customise" }).click();
  const editor = section.locator(".pl-editor");
  await editor.getByLabel("Name").fill("python pipeline");
  await setting(page, "Check each action with").locator("select").selectOption("custom");
  await expect(setting(page, "Check each action with")).toContainText("needs a VALIDATOR");

  await editor.getByRole("button", { name: "Start from the example" }).click();
  const code = editor.getByLabel("Python agents");
  await expect(code).toHaveValue(/DETECTORS = \[PressureRiseDetector\(\)\]/);
  await expect(setting(page, "Check each action with")).not.toContainText("needs a VALIDATOR");
  const example = await code.inputValue();

  await code.fill("def broken(:\n    pass\n");
  await editor.getByRole("button", { name: "Check the code" }).click();
  await expect(editor.locator(".bad-text")).toContainText("line 1");
  await code.fill(example);
  await editor.getByRole("button", { name: "Check the code" }).click();
  await expect(editor.locator(".ok-text").last()).toContainText("detectors: reactor_pressure_rise; validator: simulate_then_check");

  await editor.getByRole("button", { name: "Save and run" }).click();
  const mine = section.locator(".pl-list").nth(1).locator("li", { hasText: "python pipeline" });
  await expect(mine).toContainText("Python agents");
  await expect(mine.locator(".pill", { hasText: /running · [0-9a-f]{10}/ })).toBeVisible();

  // the detector runs on every observation of the live plant
  await page.locator(".transport button", { hasText: "+1 min" }).click();
  const detection = page.locator("details.section", { has: page.locator(":scope > summary", { hasText: "Fault detection" }) });
  await expect(detection).toContainText("Your detectors");
  await expect(detection).toContainText("reactor_pressure_rise");
  await expect(detection).toContainText("bar/min");
  expect(errors).toEqual([]);
});

test("the step diagram switches steps and stays in sync with the settings", async ({ page }) => {
  const { section } = await openPipelines(page);
  await section.locator(".pl-list li", { hasText: "LLM + digital twin" }).getByRole("button", { name: "Customise" }).click();
  const steps = section.locator(".pl-steps");
  await steps.getByRole("listitem").filter({ hasText: "Knowledge graph" }).click();
  await expect(steps.getByRole("listitem").filter({ hasText: "Knowledge graph" })).toContainText("skipped");
  await expect(setting(page, "Use the knowledge graph")).toHaveClass(/changed/);
  await steps.getByRole("listitem").filter({ hasText: "Planning" }).click();
  await expect(setting(page, "Planner").locator("select")).toHaveValue("llm");
  await steps.getByLabel("Check each action with").selectOption("judge");
  await expect(setting(page, "Check each action with").locator("select")).toHaveValue("judge");
  await section.getByRole("button", { name: "More retries" }).click();
  await expect(setting(page, "Retries after a rejected proposal").locator("input")).toHaveValue("4");
  await section.getByRole("button", { name: "apply the best safe candidate" }).click();
  await expect(section.locator(".pl-steps-loop")).toContainText("give up");
  await expect(section.locator(".pl-editor .pl-setting.changed")).toHaveCount(5);
});

test("your own wiring: a custom step runs in the live pipeline", async ({ page }) => {
  const { section, errors } = await openPipelines(page);
  await section.locator(".pl-list li", { hasText: "LLM, no validation" }).getByRole("button", { name: "Customise" }).click();
  const editor = section.locator(".pl-editor");
  await editor.getByLabel("Name").fill("wired pipeline");
  await editor.getByRole("button", { name: "Start from the example" }).click();
  await editor.getByRole("button", { name: "Add the wiring example" }).click();
  await expect(editor.getByLabel("Python agents")).toHaveValue(/def BUILD_GRAPH\(graph, nodes, routes\)/);
  await expect(editor).toContainText("wires the steps itself");
  await expect(editor.getByRole("button", { name: "Add the wiring example" })).toHaveCount(0);
  await editor.getByRole("button", { name: "Check the code" }).click();
  await expect(editor.locator(".ok-text").last()).toContainText("own wiring with steps no_throughput_changes");
  await editor.getByRole("button", { name: "Save and run" }).click();
  await expect(section.locator(".pl-list li.on")).toContainText("wired pipeline");
  await expect(page.locator(".pipeline-card")).toContainText("your wiring");
  await expect(page.locator(".pipeline-card")).toContainText("no_throughput_changes");
  expect(errors).toEqual([]);
});

test("benchmark the running pipeline on a scenario and compare with the published runs", async ({ page }) => {
  test.setTimeout(240_000);
  const { section, errors } = await openPipelines(page);
  const bench = section.locator("#pipeline-bench");
  await bench.getByLabel("Scenarios").selectOption("S01");
  await expect(bench).toContainText("No LLM connected");
  await bench.getByRole("button", { name: "Start the benchmark" }).click();
  await expect(bench).toContainText(/Running · 0 of 1 scenarios/);
  await expect(bench.getByRole("button", { name: "Stop" })).toBeVisible();
  await expect(bench).toContainText("complete · 1 of 1 scenarios", { timeout: 200_000 });
  const row = bench.locator(".bench-table tbody tr", { hasText: "S01" });
  await expect(row).toContainText("$");
  await expect(bench.locator(".bench-table thead")).toContainText("gpt-5-nano · reference");
  await expect(bench.locator(".bench-table tfoot")).toContainText("1 scored");
  await expect(bench.getByRole("button", { name: "Start the benchmark" })).toBeEnabled();
  expect(errors).toEqual([]);
});

test("a benchmark can be stopped", async ({ page }) => {
  const { section } = await openPipelines(page);
  const bench = section.locator("#pipeline-bench");
  await bench.getByRole("button", { name: "Start the benchmark" }).click();   // quick: 3 scenarios
  await expect(bench).toContainText(/Running · 0 of 3 scenarios/);
  await bench.getByRole("button", { name: "Stop" }).click();
  await expect(bench).toContainText("stopped early · 1 of 3 scenarios", { timeout: 60_000 });
  await expect(bench.locator(".bench-table tbody tr")).toContainText("(stopped early)");
});
