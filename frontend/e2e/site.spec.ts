// The project page's download section (site/index.html): the app for the visitor's system, from the newest
// release that has it. The page is served from disk and the GitHub API is mocked: no network needed.
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { extname } from "node:path";
import { fileURLToPath } from "node:url";

const SITE = fileURLToPath(new URL("../../site/", import.meta.url));
const UA = {
  windows: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
  macos: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
  linux: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
};
const asset = (name: string, mb: number) => ({ name, size: mb * 1e6, browser_download_url: `https://dl.test/${name}` });
const RC3 = { tag_name: "v1.1.0-rc.3", draft: false, prerelease: true, assets: [
  asset("OperAItor-Setup-1.1.0-rc.3.exe", 52), asset("OperAItor-Setup.exe", 52), asset("OperAItor-1.1.0-rc.3-macos-arm64.dmg", 80),
  asset("OperAItor-macOS.dmg", 80), asset("OperAItor-1.1.0-rc.3-linux-x86_64.tar.gz", 75), asset("OperAItor-Linux.tar.gz", 75)] };
const RC2 = { tag_name: "v1.1.0-rc.2", draft: false, prerelease: true, assets: [asset("OperAItor-Setup-1.1.0.exe", 52)] };
const TYPES: Record<string, string> = { ".html": "text/html", ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp" };

async function openSite(page: Page, releases: unknown[] | null) {
  await page.route("https://site.test/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\//, "") || "index.html";
    try {
      route.fulfill({ body: readFileSync(SITE + path), contentType: TYPES[extname(path)] ?? "application/octet-stream" });
    } catch {
      route.fulfill({ status: 404, body: "" });
    }
  });
  await page.route("https://api.github.com/**", (route) => releases === null
    ? route.fulfill({ status: 403, body: "rate limited" })
    : route.fulfill({ json: releases, headers: { "access-control-allow-origin": "*" } }));
  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({ body: "", contentType: "text/css" }));
  await page.goto("https://site.test/index.html");
}

for (const [system, name, file, mb] of [["windows", "Windows", "OperAItor-Setup-1.1.0-rc.3.exe", 52],
  ["macos", "macOS", "OperAItor-1.1.0-rc.3-macos-arm64.dmg", 80], ["linux", "Linux", "OperAItor-1.1.0-rc.3-linux-x86_64.tar.gz", 75]] as const) {
  test.describe(`a visitor on ${name}`, () => {
    test.use({ userAgent: UA[system] });
    test("gets the app for their system from the newest release", async ({ page }) => {
      await openSite(page, [RC3, RC2]);
      const btn = page.locator("#app-download");
      await expect(btn).toContainText(`Download for ${name} · ${mb} MB`);
      await expect(btn).toHaveAttribute("href", `https://dl.test/${file}`);
      await expect(page.locator("#app-download-note")).toContainText("Version 1.1.0-rc.3 (release candidate)");
      await expect(page.locator(`#dl-${system}`)).toHaveClass(/current/);
      await expect(page.locator("#dl-windows")).toHaveAttribute("href", "https://dl.test/OperAItor-Setup-1.1.0-rc.3.exe");
      await expect(page.locator("#dl-macos")).toHaveAttribute("href", "https://dl.test/OperAItor-1.1.0-rc.3-macos-arm64.dmg");
      await expect(page.locator("#dl-linux")).toHaveAttribute("href", "https://dl.test/OperAItor-1.1.0-rc.3-linux-x86_64.tar.gz");
    });
  });
}

test.describe("before the first release with Mac and Linux apps", () => {
  test.use({ userAgent: UA.macos });
  test("a Mac visitor is sent to the releases page; Windows still downloads", async ({ page }) => {
    await openSite(page, [RC2]);
    await expect(page.locator("#app-download")).toContainText("macOS: coming in the next release");
    await expect(page.locator("#app-download")).toHaveAttribute("href", /\/releases$/);
    await expect(page.locator("#dl-windows")).toHaveAttribute("href", "https://dl.test/OperAItor-Setup-1.1.0.exe");
  });
});

test.describe("without the GitHub API", () => {
  test.use({ userAgent: UA.linux });
  test("the links keep the latest release's fixed-name files", async ({ page }) => {
    await openSite(page, null);
    await expect(page.locator("#app-download")).toContainText("Download for Linux");
    await expect(page.locator("#app-download")).toHaveAttribute("href", /releases\/latest\/download\/OperAItor-Linux\.tar\.gz$/);
    await expect(page.locator("#dl-macos")).toHaveAttribute("href", /releases\/latest\/download\/OperAItor-macOS\.dmg$/);
  });
});

test("the page presents a beta benchmark to run, with the original runs as references", async ({ page }) => {
  await openSite(page, [RC3]);
  await expect(page.locator(".hero .pill")).toContainText("Beta");
  await expect(page.locator(".numbers")).not.toContainText("18 → 5");        // no headline result claims
  await expect(page.locator(".numbers")).toContainText("benchmark fault scenarios");
  await expect(page.locator("#benchmark")).toContainText("Bring your own agents");
  await expect(page.locator("#benchmark")).toContainText("Reference runs");
  await expect(page.locator("#benchmark .chart")).toContainText("LLM + digital twin");  // the original study's runs, kept
  await expect(page.locator("#benchmark")).not.toContainText("matched the digital twin");
  const report = page.locator(".beta a", { hasText: "Report it" });
  await expect(report).toHaveAttribute("href", /issues\/new\/choose$/);
  await expect(page.locator(".beta a", { hasText: "model scope" })).toHaveAttribute("href", /plant-model\.md#model-scope/);
});

test.describe("on a high-resolution (retina) screen", () => {
  test.use({ deviceScaleFactor: 2 });
  test("the console screenshot is sharp: the 2x image, drawn flat", async ({ page }) => {
    await openSite(page, [RC3]);
    const shot = page.locator(".window img");
    await shot.scrollIntoViewIfNeeded();
    await expect.poll(() => shot.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
    expect(await shot.evaluate((i: HTMLImageElement) => i.currentSrc)).toMatch(/plant@2x\.webp$/);
    // at least two image pixels per screen pixel, and no 3D tilt (it resamples the image and blurs the text)
    const [natural, shown] = await shot.evaluate((i: HTMLImageElement) => [i.naturalWidth * 2, i.clientWidth]);
    expect(natural).toBeGreaterThanOrEqual(shown * 2);
    expect(await page.locator(".window").evaluate((w) => getComputedStyle(w).transform)).toBe("none");
  });
});
