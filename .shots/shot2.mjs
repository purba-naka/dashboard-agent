import { chromium } from "playwright";
const WS = "01M3VWF20FQZNPWV70FD4MX3KJ";
const browser = await chromium.launch();
async function open(w, h) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, colorScheme: "dark", acceptDownloads: true });
  page.on("pageerror", (e) => console.log("PAGEERROR", e.message));
  await page.route((u) => u.port === "8000", async (r) => {
    if (r.request().url().endsWith("/events")) return r.abort();
    try {
      const res = await r.fetch({ url: r.request().url().replace(":8000", ":8001") });
      await r.fulfill({ response: res, headers: { ...res.headers(), "access-control-allow-origin": "http://localhost:3000" } });
    } catch { /* page closed */ }
  });
  await page.goto("http://localhost:3000/w/" + WS, { waitUntil: "load" });
  await page.waitForTimeout(12000);
  return page;
}
for (const [w, h] of [[1920, 1080], [1366, 768]]) {
  const page = await open(w, h);
  await page.screenshot({ path: `s-${w}.png` });
  await page.screenshot({ path: `s-${w}-full.png`, fullPage: true });
  if (w === 1366) {
    await page.getByRole("button", { name: /Sembunyikan data/ }).click();
    await page.getByRole("button", { name: /Sembunyikan chat/ }).click();
    await page.waitForTimeout(2500);
    await page.screenshot({ path: "s-1366-collapsed.png" });
  } else {
    const d = page.locator("details", { hasText: "rovinsi" }).first();
    await d.locator("summary").click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: "s-1920-filter.png" });
    await d.locator("summary").click();
    for (const [name, file] of [["Ekspor PNG", "export.png"], ["Ekspor PDF", "export.pdf"]]) {
      const dl = page.waitForEvent("download", { timeout: 60000 });
      await page.getByRole("button", { name }).click();
      await (await dl).saveAs(file);
      console.log("saved", file);
    }
  }
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.close();
}
await browser.close();
