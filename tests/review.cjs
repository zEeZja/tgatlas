const { chromium } = require("playwright");
const fs = require("node:fs"),
  path = require("node:path"),
  os = require("node:os"),
  assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const http = require("node:http");
const server = http.createServer((req, res) => {
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.end(
    fs.readFileSync(
      path.resolve(__dirname, "../docs/review/classification-sample.html"),
    ),
  );
});
(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atlas-review-"));
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROMIUM_PATH ||
      (fs.existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined),
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 1000 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    await page.goto("http://127.0.0.1:" + server.address().port);
    await page.locator("#topic").selectOption("gaming");
    await page.locator("#scope").selectOption("global");
    await page.locator("#tags").selectOption(["ai"]);
    await page.locator("#reviewed").check();
    assert(
      (await page.locator("#progress").textContent()).includes("проверено 1"),
    );
    await page.reload();
    assert(await page.locator("#reviewed").isChecked());
    assert.equal(await page.locator("#topic").inputValue(), "gaming");
    await page.locator("#use-suggestion").click();
    assert.equal(await page.locator("#topic").inputValue(), "gaming");
    assert(await page.locator("#reviewed").isChecked());
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.locator("#download").click(),
    ]);
    const output = path.join(dir, "review.csv");
    await download.saveAs(output);
    const rows = JSON.parse(
      execFileSync(
        process.env.PYTHON || "python3",
        [
          "-c",
          "import json,sys;from src.classification_review import read_review;print(json.dumps(read_review(sys.argv[1])))",
          output,
        ],
        { cwd: path.resolve(__dirname, ".."), encoding: "utf8" },
      ),
    );
    assert.equal(rows.length, 1);
    assert.equal(rows[0].expected_topic, "gaming");
    assert.equal(rows[0].expected_scope, "global");
    assert.equal(rows[0].expected_secondary_topics, "ai");
    // Restore the old form's exact 19-column records, including 60 confirmations.
    await page.evaluate(() => {
      const legacy = rows.map((r, i) =>
        Object.fromEntries(payload.fields.slice(0, 19).map((k) => [k, r[k]])),
      );
      legacy.forEach((r, i) => {
        r.reviewed = i < 60 ? "yes" : "no";
      });
      Object.assign(legacy[0], {
        expected_topic: "business",
        expected_secondary_topics: "",
        expected_scope: "country",
        expected_regions: "",
        expected_country: "RU",
      });
      legacy[1].expected_topic = "other";
      localStorage.setItem(storageKey, JSON.stringify(legacy));
    });
    await page.reload();
    assert(
      (await page.locator("#progress").textContent()).includes("проверено 60"),
    );
    assert.equal(await page.locator("#topic").inputValue(), "business");
    assert.equal(await page.locator("#scope").inputValue(), "country");
    assert.equal(
      await page.evaluate(() => rows[1].expected_group),
      "other_group",
    );
    const legacyFile = path.join(dir, "legacy.csv");
    execFileSync(
      process.env.PYTHON || "python3",
      [
        "-c",
        `
import csv,sys
from src.classification_review import LEGACY_FIELDS
with open('docs/review/classification-sample.csv',encoding='utf-8-sig',newline='') as f:
 rows=list(csv.DictReader(f,delimiter=';'))
for i,r in enumerate(rows): r['reviewed']='yes' if i<60 else 'no'
rows[0].update(expected_topic='ai',expected_secondary_topics='',expected_scope='global',expected_regions='',expected_country='',note='Импорт; кавычки "и перенос\\nстроки"')
with open(sys.argv[1],'w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,LEGACY_FIELDS,delimiter=';');w.writeheader();w.writerows(rows)
`,
        legacyFile,
      ],
      { cwd: path.resolve(__dirname, "..") },
    );
    await page.locator("#import").setInputFiles(legacyFile);
    await page.waitForFunction(() =>
      document
        .querySelector("#import-status")
        .textContent.includes("Загружено 200"),
    );
    assert.equal(await page.locator("#topic").inputValue(), "ai");
    assert(
      (await page.locator("#progress").textContent()).includes("проверено 60"),
    );
    // An owner can confirm only a group, an independent format and no territory.
    await page.locator("#topic-group").selectOption("enterprise");
    assert.equal(await page.locator("#topic").inputValue(), "other");
    await page.locator("#formats").selectOption(["analysis"]);
    await page.locator("#scope").selectOption("nonlocal");
    assert.equal(await page.locator("#country").inputValue(), "");
    assert(await page.locator("#country").isDisabled());
    await page.locator("#reason").selectOption("missing_topic");
    await page.locator("#proposed-topic").fill("Маркетплейсы");
    await page.locator("#reviewed").check();
    const [v2Download] = await Promise.all([
      page.waitForEvent("download"),
      page.locator("#download").click(),
    ]);
    const v2File = path.join(dir, "v2.csv");
    await v2Download.saveAs(v2File);
    const v2 = JSON.parse(
      execFileSync(
        process.env.PYTHON || "python3",
        [
          "-c",
          "import json,sys;from src.classification_review import read_review;print(json.dumps(read_review(sys.argv[1])))",
          v2File,
        ],
        { cwd: path.resolve(__dirname, ".."), encoding: "utf8" },
      ),
    );
    assert.equal(v2.length, 60);
    assert.equal(v2[0].expected_group, "enterprise");
    assert.equal(v2[0].expected_topic, "other");
    assert.equal(v2[0].expected_formats, "analysis");
    assert.equal(v2[0].expected_scope, "nonlocal");
    assert.equal(v2[0].proposed_topic, "Маркетплейсы");
    assert.equal(v2[0].review_schema_version, "3");
    assert.equal(v2[0].topic_reviewed, "yes");
    assert.equal(v2[0].geography_reviewed, "yes");
    assert.equal(v2[1].review_schema_version, "1");
    // A bad geography must not discard a confirmed topic or invent a country.
    await page.locator("#scope").selectOption("country");
    await page.locator("#geography-reviewed").click();
    assert(!(await page.locator("#geography-reviewed").isChecked()));
    assert(await page.locator("#topic-reviewed").isChecked());
    assert.equal(await page.locator("#country").inputValue(), "");
    assert((await page.locator("#validation").innerText()).includes("Подтверждение не принято"));
    await page.locator("#choose-nonlocal").click();
    await page.locator("#geography-reviewed").check();
    assert(await page.locator("#reviewed").isChecked());
    await page.locator("#next-unreviewed").click();
    assert(!(await page.locator("#reviewed").isChecked()));
    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      !(await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      )),
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS: offline owner review, labels and tags, explicit confirmation, persistence, CSV accepted by Python, mobile; no JS errors.",
    );
  } finally {
    await browser.close();
    server.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
