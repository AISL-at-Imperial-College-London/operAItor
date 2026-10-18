// Feedback / bug reports: the console fills in a GitHub issue (no server, no token) for every way OperAItor runs.
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

async function openFeedback(page: import("@playwright/test").Page) {
  await page.goto("/");
  await expect(page.locator(".clock")).not.toHaveText("—");   // connected: the session (and its server facts) arrived
  await page.getByRole("button", { name: "Feedback" }).click();
  return page.getByRole("dialog", { name: "Report a bug or give feedback" });
}

test("a physics report opens the physics issue form, filled in with diagnostics", async ({ page }) => {
  const dialog = await openFeedback(page);
  await dialog.getByRole("radio", { name: "The plant behaves oddly" }).click();
  await expect(dialog.getByRole("button", { name: /Continue on GitHub/ })).toBeDisabled();   // title and description first
  await dialog.getByLabel("Title").fill("Reboiler level rises after a feed cut");
  await dialog.getByLabel("What happened, and what you expected").fill("Cut the feed to 80; the reboiler level kept rising.");
  const go = dialog.getByRole("link", { name: /Continue on GitHub/ });
  await expect(go).toHaveAttribute("href", /version=\d/);
  const url = new URL((await go.getAttribute("href"))!);
  expect(url.origin + url.pathname).toBe("https://github.com/AISL-at-Imperial-College-London/operAItor/issues/new");
  expect(url.searchParams.get("template")).toBe("physics.yml");
  expect(url.searchParams.get("title")).toBe("[physics] Reboiler level rises after a feed cut");
  expect(url.searchParams.get("what")).toContain("reboiler level kept rising");
  expect(url.searchParams.get("version")).toMatch(/^\d+\.\d+\.\d+.* \(source\)$/);   // the e2e server runs from source
  const diag = url.searchParams.get("diagnostics")!;
  for (const part of ["OperAItor", "Pipeline:", "Model:", "Plant: nominal", "Active alarms:"]) expect(diag).toContain(part);
  expect(url.toString().length).toBeLessThan(8000);
  await expect(go).toHaveAttribute("target", "_blank");
});

test("feedback without diagnostics, and a downloadable report for people without GitHub", async ({ page }) => {
  const dialog = await openFeedback(page);
  await dialog.getByRole("radio", { name: "Feedback or an idea" }).click();
  await dialog.getByLabel("Title").fill("Show the benchmark score as a chart");
  await dialog.getByLabel("Your feedback").fill("A bar per scenario would make the comparison easier.");
  await dialog.getByLabel(/Attach diagnostics/).uncheck();
  const url = new URL((await dialog.getByRole("link", { name: /Continue on GitHub/ }).getAttribute("href"))!);
  expect(url.searchParams.get("template")).toBe("feedback.yml");
  expect(url.searchParams.get("diagnostics")).toBeNull();
  const [download] = await Promise.all([page.waitForEvent("download"), dialog.getByRole("button", { name: "Download report" }).click()]);
  const text = readFileSync(await download.path(), "utf-8");
  expect(text).toContain("[feedback] Show the benchmark score as a chart");
  expect(text).not.toContain("--- Diagnostics ---");
  await expect(dialog.getByRole("link", { name: "ones we'd love help with" })).toHaveAttribute("href", /label%3A%22help\+wanted%22/);
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toHaveCount(0);
});
