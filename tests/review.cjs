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
    await page.locator('#tags input[value="ai"]').check();
    await page.locator("#reviewed").check();
    assert(
      (await page.locator("#progress").textContent()).includes("проверено 1"),
    );
    await page.reload();
    assert(await page.locator("#reviewed").isChecked());
    assert.equal(await page.locator("#topic").inputValue(), "gaming");
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
