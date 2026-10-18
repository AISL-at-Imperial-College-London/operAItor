// Connecting the agents to an LLM. When OperAItor runs on a computer with Ollama, the server connects the agents
// to it by itself; the API providers (Groq, Gemini, OpenRouter, OpenAI) must still be one click away.
import { expect, test, type Page } from "@playwright/test";

const SERVER_OLLAMA = { provider: "ollama", label: "Ollama on this machine", model: "qwen3:8b", free: true, url: null };

/** The test server has no LLM; make its first greeting say "the agents use this computer's Ollama". Returns the
 *  messages the console sends to the server. */
async function withServerOllama(page: Page) {
  const sent: Record<string, unknown>[] = [];
  await page.routeWebSocket(/\/ws/, (ws) => {
    const server = ws.connectToServer();
    let faked = false;
    server.onMessage((raw) => {
      const msg = JSON.parse(String(raw));
      if (!faked && msg.type === "hello" && msg.session) {
        faked = true;
        Object.assign(msg.session.llm, { active: true, key_source: "server", connection: SERVER_OLLAMA, model: "qwen3:8b", reason: "" });
        ws.send(JSON.stringify(msg));
        return;
      }
      ws.send(raw);
    });
    ws.onMessage((m) => {
      sent.push(JSON.parse(String(m)));
      server.send(m);
    });
  });
  return sent;
}

async function agentSettings(page: Page) {
  await page.goto("/");
  await page.locator(".tabs button", { hasText: "Agents" }).click();
  return page.locator(".card.settings");
}

test("with this computer's Ollama connected, the API providers are still offered", async ({ page }) => {
  await withServerOllama(page);
  const settings = await agentSettings(page);
  const card = settings.locator(".key-card.on");
  await expect(card).toContainText("LLM agents on");
  await expect(card).toContainText("qwen3:8b");
  await expect(card.getByRole("button", { name: "Change model" })).toBeVisible();

  await card.getByRole("button", { name: "Use another provider" }).click();
  const provider = card.getByLabel("Provider");
  for (const name of ["Groq", "Google Gemini", "OpenRouter", "OpenAI"]) {
    await expect(provider.locator("option", { hasText: name })).toHaveCount(1);
  }
  await card.getByRole("button", { name: "Cancel" }).click();
  await expect(card.getByLabel("Provider")).toHaveCount(0);
});

test("switch from this computer's Ollama to Groq: the browser checks the key, and the server never gets it", async ({ page }) => {
  const sent = await withServerOllama(page);
  const keysSeen: string[] = [];
  await page.route("https://api.groq.com/**", (route) => {
    keysSeen.push(route.request().headers()["authorization"] ?? "");
    return route.fulfill({ json: { data: [{ id: "llama-3.3-70b-versatile" }] }, headers: { "access-control-allow-origin": "*" } });
  });
  const settings = await agentSettings(page);
  const card = settings.locator(".key-card.on");
  await card.getByRole("button", { name: "Use another provider" }).click();
  await card.getByLabel("Provider").selectOption("groq");
  await card.getByLabel("API key").fill("gsk-test-key");
  await card.getByRole("button", { name: "Connect" }).click();
  // (the test server runs without LLMs, so it refuses the switch itself; the server side is tested in test_server_app)
  await expect.poll(() => sent.find((m) => m.type === "set_llm")).toBeTruthy();
  expect(keysSeen).toContain("Bearer gsk-test-key");   // the key was checked from the browser...
  const request = sent.find((m) => m.type === "set_llm")!;
  expect(request).toMatchObject({ provider: "groq", model: "llama-3.3-70b-versatile", in_browser: true });
  expect(JSON.stringify(sent)).not.toContain("gsk-test-key");  // ...and never sent to the server
});

test("with no LLM connected, the provider form is shown straight away", async ({ page }) => {
  const settings = await agentSettings(page);
  await expect(settings.getByLabel("Provider")).toBeVisible();
  await expect(settings.getByLabel("Provider").locator("option", { hasText: "Groq" })).toHaveCount(1);
});
