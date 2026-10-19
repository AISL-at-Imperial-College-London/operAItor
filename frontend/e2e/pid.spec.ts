// The Plant tab's P&ID view: the drawing with live controller values, latched safety functions and the C&E matrix.
import { expect, test } from "@playwright/test";

test("switch to the P&ID, and a reflux pump trip lights up the dry-tray safety function", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await expect(page.locator(".clock")).not.toHaveText("—");
  await page.getByRole("radio", { name: "P&ID" }).click();
  const pid = page.locator(".pid svg");
  await expect(pid).toBeVisible();
  await expect(pid.locator('[data-pv="R_TIC"]')).toHaveText(/^\d+\.\d K$/);           // live controller values
  await expect(pid.locator('[data-pv="R_PIC"]')).toHaveText(/^\d\.\d{3} bar$/);
  const rows = page.locator(".ce-table tbody tr");
  await expect(rows).toHaveCount(11);
  await expect(page.locator(".ce-table tr.on")).toHaveCount(0);                         // nothing tripped yet
  await expect(rows.filter({ hasText: "COL_P_HH" })).toContainText("OPEN");             // condenser cooling water

  // the choice is remembered
  await page.reload();
  await expect(page.locator(".pid svg")).toBeVisible();

  // trip the reflux pump; without reflux the trays drain and COL_L_LL trips the plant
  await page.locator(".drawer-buttons button", { hasText: "Faults" }).click();
  await page.locator(".panel-body").filter({ hasText: "Inject a fault" }).getByRole("button", { name: "Inject", exact: true }).click();
  await page.getByRole("button", { name: "Close panel" }).click();
  // The trip ends the episode and "+1 min" disappears, possibly between the check and the click: a short click
  // that may miss, then look again.
  const step = page.getByRole("button", { name: "+1 min" });
  for (let i = 0; i < 20 && (await page.locator(".ce-table tr.on").count()) === 0; i++) {
    await step.click({ timeout: 5_000 }).catch(() => {});
    await page.waitForTimeout(400);
  }
  await expect(page.locator('.pid [data-sif="COL_L_LL"]')).toHaveClass(/on/, { timeout: 15_000 });
  await expect(page.locator(".ce-table tr.on").filter({ hasText: "COL_L_LL" })).toHaveCount(1);
  await expect(page.locator(".ce-table tr.on").filter({ hasText: "COL_L_LL" })).toContainText("LATCH");
  await expect(page.locator('.pid [data-sif="R_T_HH"]')).not.toHaveClass(/on/);
});

test("the process graphic is still the default view", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("radio", { name: "Process graphic" })).toHaveAttribute("aria-checked", "true");
  await expect(page.locator(".pid")).toHaveCount(0);
});
