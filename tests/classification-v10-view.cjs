/* Autonomous file:// viewer: filters, safe text, persistence, JSON round-trip. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { spawnSync } = require("node:child_process");
const os = require("node:os");

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "atlas-v10-view-"));
  const html = path.join(temp, "view.html");
  const generated = spawnSync("python", ["-B", "-c", `
import sys, copy
from tests.test_classification_v10_view import fixture
from src.classification_v10_view import build_view
snapshot, results = fixture()
for i in range(1, 30):
 row = dict(snapshot[0], username=f'demo_{i}', name=f'Канал {i}')
 snapshot.append(row)
 prediction = copy.deepcopy(results['channels']['demo'])
 prediction['classification_profile']['name'] = row['name']
 if i % 2: prediction['subcategory'] = 'tech'
 results['channels'][row['username']] = prediction
open(sys.argv[1], 'w', encoding='utf-8').write(build_view(snapshot, results))
`, html], {cwd: path.resolve(__dirname, ".."), encoding: "utf8"});
  assert.equal(generated.status, 0, generated.stderr);
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH, args: ["--no-sandbox"]});
  try {
    const page = await browser.newPage({acceptDownloads: true});
    const errors = [], requests = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => { if (/^https?:/.test(r.url())) requests.push(r.url()); });
    await page.goto(pathToFileURL(html).href);
    assert.equal(await page.locator(".card").count(), 25);
    assert((await page.locator("#stats").innerText()).includes("Каналов: 30"));
    assert((await page.locator(".card h2").first().innerText()).includes("<script>"));
    assert.equal(await page.locator(".card script").count(), 0);
    assert((await page.locator(".candidates").first().innerText()).includes("-0.200"));
    await page.locator("#next").click();
    assert.equal(await page.locator(".card").count(), 5);
    await page.locator("#mode").selectOption("changed");
    assert.equal(await page.locator(".card").count(), 15);
    await page.locator("#predicted").selectOption("tech");
    assert.equal(await page.locator(".card").count(), 0);
    await page.locator("#predicted").selectOption("");
    await page.locator("#mode").selectOption("all");
    await page.locator("#search").fill("@demo_29");
    assert.equal(await page.locator(".card").count(), 1);
    await page.getByLabel("Моя оценка @demo_29", {exact: true}).selectOption("wrong");
    await page.getByLabel("Правильная тема @demo_29", {exact: true}).selectOption("science");
    await page.getByLabel("Комментарий @demo_29", {exact: true}).fill("Проверил вручную");
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export").click();
    const download = await downloadPromise;
    const jsonPath = path.join(temp, "reviews.json"); await download.saveAs(jsonPath);
    const saved = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
    assert.equal(saved.reviews.demo_29.verdict, "wrong");
    assert.equal(saved.reviews.demo_29.topic, "science");
    await page.reload();
    await page.locator("#mode").selectOption("wrong");
    assert.equal(await page.locator(".card").count(), 1);
    assert.equal(await page.getByLabel("Комментарий @demo_29", {exact: true}).inputValue(), "Проверил вручную");
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.locator("#import").setInputFiles(jsonPath);
    await page.waitForFunction(() => document.getElementById("status").textContent.includes("Оценки загружены"));
    await page.locator("#mode").selectOption("wrong");
    assert.equal(await page.locator(".card").count(), 1);
    saved.fingerprint = "foreign"; fs.writeFileSync(jsonPath, JSON.stringify(saved));
    await page.locator("#import").setInputFiles(jsonPath);
    await page.waitForFunction(() => document.getElementById("status").textContent.includes("Не удалось загрузить"));
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    assert.deepEqual(errors, []); assert.deepEqual(requests, []);
    console.log("PASS: V10 file viewer, filters, paging, XSS, local persistence, review export/import, mobile, no network.");
  } finally {
    await browser.close();
    fs.rmSync(temp, {recursive: true, force: true});
  }
})().catch((error) => {console.error(error); process.exitCode = 1;});
