// The first-visit tour: it starts by itself, waits for the visitor at the steps that ask for something, and is
// remembered once finished. (The console skips the tour for automated browsers, so these tests pretend not to be one.)
import { expect, test, type Page } from "@playwright/test";

const SHOTS = process.env.TOUR_SHOTS;   // a folder: save a screenshot of each step (for looking at them)

async function asVisitor(page: Page) {
  await page.addInitScript(() => Object.defineProperty(Navigator.prototype, "webdriver", { get: () => false }));
}

async function at(page: Page, title: string) {
  const card = page.locator(".tour-card");
  await expect(card.locator("h3")).toHaveText(title);
  if (SHOTS) await page.waitForTimeout(400);   // the spotlight glides to its target
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${title.replace(/[^a-z]+/gi, "-")}.png` });
  return card;
}

test("first visit: the tour walks through running the plant, a fault and the agents, then stays away", async ({ page }) => {
  test.setTimeout(90_000);
  await asVisitor(page);
  await page.goto("/");
  let card = await at(page, "Welcome to the control room");
  await card.getByRole("button", { name: "Show me around" }).click();
  card = await at(page, "Plant status");
  await card.getByRole("button", { name: "Next" }).click();

  // the steps that ask for something wait until the visitor does it, through the spotlight
  card = await at(page, "Start the clock");
  await expect(card).toContainText("Your turn: Press ▶ Run");
  await page.locator(".transport").getByRole("button", { name: "▶ Run" }).click();
  await expect(card.locator(".tour-task")).toHaveText("✓ Done");
  card = await at(page, "The plant, live");
  await page.locator(".transport").getByRole("button", { name: "❚❚ Pause" }).click();
  await card.getByRole("button", { name: "Next" }).click();

  card = await at(page, "Your controls");
  await page.locator(".drawer-buttons").getByRole("button", { name: "Operate" }).click();
  card = await at(page, "Break something");
  await page.locator(".drawer-buttons").getByRole("button", { name: "Faults" }).click();
  card = await at(page, "Inject a fault");
  await page.locator("aside.drawer").getByRole("button", { name: "Inject", exact: true }).click();
  card = await at(page, "Meet the agents");
  await expect(page.locator("aside.drawer")).toHaveCount(0);            // the tour closed the panel for the tabs
  await page.locator('.tabs [data-tab="agents"]').click();
  card = await at(page, "Give them a model");
  await expect(page.locator(".tour-hole")).toBeVisible();              // the agent settings are pointed at
  for (const next of ["Watch them decide", "Make it yours", "Or operate it yourself", "You are ready"]) {
    await card.getByRole("button", { name: "Next" }).click();
    card = await at(page, next);
  }
  await card.getByRole("button", { name: "Start playing" }).click();
  await expect(page.locator(".tour")).toHaveCount(0);

  // finished once: not again on the next visit, but the header replays it
  await page.reload();
  await expect(page.locator(".clock")).not.toHaveText("—");
  await page.waitForTimeout(500);
  await expect(page.locator(".tour")).toHaveCount(0);
  await page.getByRole("button", { name: "Tour" }).click();
  await at(page, "Welcome to the control room");
});

test("the tour can be skipped, and a step's task can be skipped too", async ({ page }) => {
  await asVisitor(page);
  await page.goto("/");
  let card = await at(page, "Welcome to the control room");
  await card.getByRole("button", { name: "Show me around" }).click();
  await card.getByRole("button", { name: "Next" }).click();
  card = await at(page, "Start the clock");
  await card.getByRole("button", { name: "Skip this step" }).click();
  await at(page, "The plant, live");
  await card.getByRole("button", { name: "Back" }).click();
  await at(page, "Start the clock");
  await page.keyboard.press("Escape");
  await expect(page.locator(".tour")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".clock")).not.toHaveText("—");
  await page.waitForTimeout(500);
  await expect(page.locator(".tour")).toHaveCount(0);                   // ended = seen
});

test("automated browsers (the other tests) get no tour", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".clock")).not.toHaveText("—");
  await page.waitForTimeout(500);
  await expect(page.locator(".tour")).toHaveCount(0);
});

test("on a phone, the tour card fits the screen and leaves its target visible", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await asVisitor(page);
  await page.goto("/");
  let card = await at(page, "Welcome to the control room");
  await card.getByRole("button", { name: "Show me around" }).click();
  await card.getByRole("button", { name: "Next" }).click();
  card = await at(page, "Start the clock");
  await page.waitForTimeout(400);
  const c = (await card.boundingBox())!;
  const hole = (await page.locator(".tour-hole").boundingBox())!;
  expect(c.x).toBeGreaterThanOrEqual(0);
  expect(c.x + c.width).toBeLessThanOrEqual(390);
  expect(c.y + c.height).toBeLessThanOrEqual(800);
  expect(c.y >= hole.y + hole.height || c.y + c.height <= hole.y).toBe(true);   // not on top of the Run button
  await page.locator(".transport").getByRole("button", { name: "▶ Run" }).click();
  await at(page, "The plant, live");
});
