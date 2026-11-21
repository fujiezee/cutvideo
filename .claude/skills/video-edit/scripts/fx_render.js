// Render an fx page frame by frame with headless Chrome; transparent PNGs out.
// usage: node fx_render.js page.html outDir W H fps duration
function load() {
  for (const m of ["puppeteer-core", "puppeteer"]) {
    try { return require(m); } catch (e) { /* try next */ }
  }
  console.error("puppeteer not found: npm i -g puppeteer-core (or set NODE_PATH)");
  process.exit(2);
}
const puppeteer = load();
const fs = require("fs");
const path = require("path");

const CHROME = process.env.CHROME_PATH || [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
].find((p) => fs.existsSync(p));

(async () => {
  const [html, outDir, W, H, fps, dur] = process.argv.slice(2);
  const w = parseInt(W), h = parseInt(H), rate = parseFloat(fps), seconds = parseFloat(dur);
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: CHROME,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
           "--allow-file-access-from-files", "--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (e) => console.error("page error:", e.message));
  await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
  await page.goto("file://" + path.resolve(html));
  await page.waitForFunction("window.fxReady === true", { timeout: 20000 });
  const n = Math.max(1, Math.round(seconds * rate));
  for (let i = 0; i < n; i++) {
    await page.evaluate((t) => window.renderFrame(t), i / rate);
    await page.screenshot({ path: path.join(outDir, "f" + String(i).padStart(5, "0") + ".png"),
                            omitBackground: false, type: "png" });
  }
  await browser.close();
  console.log(n);
})().catch((e) => { console.error(e); process.exit(1); });
