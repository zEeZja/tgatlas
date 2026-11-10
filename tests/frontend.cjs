/* npm install && npx playwright install chromium && npm run test:frontend */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const root = path.resolve(__dirname, "../web");
const server = http.createServer((req, res) => {
  let file = path.resolve(
    root,
    "." + decodeURIComponent(new URL(req.url, "http://localhost").pathname),
  );
  if (!file.startsWith(root + path.sep) && file !== root) {
    res.writeHead(403);
    res.end();
    return;
  }
  if (file === root || file === root + path.sep)
    file = path.join(root, "index.html");
  fs.readFile(file, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end();
      return;
    }
    const mime = {
      ".html": "text/html",
      ".js": "application/javascript",
      ".css": "text/css",
      ".svg": "image/svg+xml",
    };
    res.setHeader(
      "Content-Type",
      mime[path.extname(file)] || "application/octet-stream",
    );
    res.end(data);
  });
});
(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const url = "http://127.0.0.1:" + server.address().port;
  const executablePath =
    process.env.CHROMIUM_PATH ||
    (fs.existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined);
  const browser = await chromium.launch({
    executablePath,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
      reducedMotion: "reduce",
    });
    const errors = [],
      requests = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => requests.push(r.url()));
    await page.goto(url);
    await page.waitForFunction(
      () => document.querySelector("#count").textContent === "779",
    );
    await page.locator("#relief-toggle").click();
    assert.equal(
      await page.locator("#relief-toggle").getAttribute("aria-pressed"),
      "false",
    );
    await page.locator("#relief-toggle").click();
    assert.equal(
      await page.locator(".chip").filter({ hasText: "Один регион" }).count(),
      0,
    );
    assert.equal(await page.locator(".result").count(), 200);
    await page.locator(".load-more").click();
    assert.equal(await page.locator(".result").count(), 400);
    assert((await page.locator("#map-summary").innerText()).includes("779"));
    assert(
      !requests.some((r) => r.includes("geo-map") || r.includes("geo_map")),
    );
    await page.locator("#search").fill("@breakingmash");
    await page.waitForTimeout(220);
    assert.equal(await page.locator("#count").textContent(), "1");
    await page.locator(".result").click();
    assert(await page.locator("#detail").isVisible());
    await page.keyboard.press("Escape");
    await page.locator("#clear").click();
    await page.locator("#region").selectOption("Москва");
    assert.equal(await page.locator("#count").textContent(), "167");
    await page.locator("#group-by").selectOption("region");
    assert.equal(await page.locator("#count").textContent(), "167");
    assert(
      (await page.locator("#group-caption").innerText()).includes(
        "По регионам",
      ),
    );
    await page.locator(".reset-btn").click();
    await page.locator("#group-by").selectOption("topic");
    await page.locator("#search").fill("nonexistentzzzzzz123");
    await page.waitForTimeout(220);
    assert.equal(await page.locator("#count").textContent(), "0");
    await page.locator("#fit-map").click();
    await page.locator(".reset-btn").click();
    await page.locator("#category").selectOption("tech");
    assert(Number(await page.locator("#count").textContent()) > 0);
    await page.locator(".reset-btn").click();
    await page.locator("#sort").selectOption("name");
    const names = await page.locator(".result-name").allTextContents();
    assert.deepEqual(
      names,
      [...names].sort((a, b) => a.localeCompare(b, "ru")),
    );
    await page.locator("#sort").selectOption("popular");
    await page.locator("#category").selectOption("politics");
    assert(Number(await page.locator("#count").textContent()) > 0);
    await page.locator(".result").first().click();
    assert((await page.locator("#detail").innerText()).includes("Политика"));
    await page.keyboard.press("Escape");
    await page.locator(".reset-btn").click();
    await page.locator(".result").first().click();
    await page.locator(".topic-edit summary").click();
    await page.locator("#topic-correction").selectOption("gaming");
    await page.locator("#save-topic").click();
    assert(
      (await page.locator(".topic-explanation").innerText()).includes(
        "Выбрано вами",
      ),
    );
    assert((await page.locator(".detail-badges").innerText()).includes("Игры"));
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#count").textContent === "779",
    );
    await page.locator(".result").first().click();
    assert((await page.locator(".detail-badges").innerText()).includes("Игры"));
    await page.locator(".topic-edit summary").click();
    await page.locator("#topic-correction").selectOption("");
    await page.locator("#save-topic").click();
    assert(
      (await page.locator(".detail-badges").innerText()).includes("Новости"),
    );
    await page.keyboard.press("Escape");
    await page.locator("#zoom-in").click();
    await page.locator("#zoom-out").click();
    await page.locator("#fit-map").click();
    // Pick an unoccluded node using the deterministic public layout.
    const pos = await page.evaluate(() => {
      const r = document.querySelector("#map").getBoundingClientRect();
      const layout = Layout.buildClusterLayout(
        TG_DATA.channels.map(Topics.enrich),
      );
      const s =
        Math.min(r.width / layout.width, r.height / layout.height) * 0.95;
      return layout.nodes
        .map((n) => ({
          x: r.x + r.width / 2 + (n.x - layout.width / 2) * s,
          y: r.y + r.height / 2 + (n.y - layout.height / 2) * s,
        }))
        .find(
          (p) =>
            p.x > r.x + 260 &&
            p.x < r.right - 150 &&
            p.y > r.y + 220 &&
            p.y < r.bottom - 220,
        );
    });
    assert(pos);
    await page.mouse.click(pos.x, pos.y);
    assert(await page.locator("#detail").isVisible());
    await page.keyboard.press("Escape");
    for (const size of [
      { width: 1280, height: 720 },
      { width: 768, height: 1024 },
      { width: 390, height: 844 },
      { width: 360, height: 640 },
    ]) {
      await page.setViewportSize(size);
      assert(
        !(await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        )),
      );
    }
    await page.locator("#mobile-toggle").click();
    await page.locator("#group-by").selectOption("region");
    await page.locator("#group-by").selectOption("topic");
    await page.locator("#mobile-toggle").click();
    await page.locator(".result").first().click();
    assert(await page.locator("#detail").isVisible());
    await page.keyboard.press("Escape");
    await page.locator("#help-toggle").click();
    assert(await page.locator("#help").isVisible());
    await page.keyboard.press("Escape");
    // Real API works without any geographic export, including channels lacking a region.
    const channel = {
      id: "api",
      username: "api",
      name: "Новости науки",
      category: "news",
      subcategory: "general",
      country: "DE",
      url: "https://t.me/api",
    };
    await page.route("**/api.js", (route) =>
      route.fulfill({
        contentType: "application/javascript",
        body:
          fs.readFileSync(path.join(root, "api.js"), "utf8") +
          "\nApi.CONFIG.mode='http';Api.CONFIG.httpBase='';",
      }),
    );
    await page.route("**/api/channels", (route) =>
      route.fulfill({ json: { channels: [channel] } }),
    );
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#count").textContent === "1",
    );
    assert(!(await page.locator("#banner").isVisible()));
    assert((await page.locator("#map-summary").textContent()).includes("1"));
    await page.locator("#mobile-toggle").click();
    await page.locator("#mobile-toggle").click();
    await page.locator(".result").click();
    assert(
      (await page.locator(".detail-badges").innerText()).includes("Наука"),
    );
    assert(!requests.some((r) => r.includes("geo-map")));
    // Regression cases from the user's real screenshots, with public profile descriptions.
    const screenshots = [
      {
        id: "murmansknow",
        username: "murmansknow",
        name: "Мурманск сейчас",
        description:
          "Поделиться новостью: @murmansknow_bot. Разместить рекламу: @adMurmansk",
        subcategory: "other",
        region_review_required: true,
        country: "RU",
      },
      {
        id: "khimki_podslushano",
        username: "khimki_podslushano",
        name: "Подслушано Химки",
        description: "По рекламе: @Sofia_Reklama",
        subcategory: "general",
        region_review_required: true,
        country: "RU",
      },
    ].map((c) => ({ ...c, url: "https://t.me/" + c.username }));
    await page.unroute("**/api/channels");
    await page.route("**/api/channels", (route) =>
      route.fulfill({ json: { channels: screenshots } }),
    );
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#count").textContent === "2",
    );
    await page.locator(".result").first().click();
    assert(
      (await page.locator(".detail-badges").innerText()).includes("Новости"),
    );
    assert(
      (await page.locator(".detail-badges").innerText()).includes(
        "Мурманская область",
      ),
    );
    await page.keyboard.press("Escape");
    await page.locator("#mobile-toggle").click();
    await page.locator(".result").nth(1).click();
    assert(
      (await page.locator(".detail-badges").innerText()).includes(
        "Московская область",
      ),
    );
    // Opt-in: managed Chromium may prohibit file:// navigation by administrator policy.
    // Category first, then coverage and contextual regions. Unknown is not global.
    const hierarchyRows = [
      {
        id: "p1",
        username: "p1",
        name: "Политика Москвы",
        subcategory: "politics",
        region: "Москва",
        country: "RU",
      },
      {
        id: "p2",
        username: "p2",
        name: "Политика регионов",
        subcategory: "politics",
        regions: ["Москва", "Татарстан"],
        country: "RU",
      },
      {
        id: "p3",
        username: "p3",
        name: "Политика мира",
        subcategory: "politics",
        geographic_scope: "global",
      },
      {
        id: "p4",
        username: "p4",
        name: "Политика",
        subcategory: "politics",
        country: "RU",
      },
      {
        id: "s1",
        username: "s1",
        name: "Спорт",
        subcategory: "sport",
        region: "Омск",
        country: "RU",
      },
    ].map((c) => ({ ...c, url: "https://t.me/" + c.username }));
    await page.unroute("**/api/channels");
    await page.route("**/api/channels", (route) =>
      route.fulfill({ json: { channels: hierarchyRows } }),
    );
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#count").textContent === "5",
    );
    await page.locator("#category").selectOption("politics");
    assert.equal(await page.locator("#count").textContent(), "4");
    assert(!(await page.locator("#region").textContent()).includes("Омск"));
    await page
      .locator(".chip")
      .filter({ hasText: "Несколько регионов" })
      .click();
    assert.equal(await page.locator("#count").textContent(), "1");
    await page.locator("#region").selectOption("Татарстан");
    assert.equal(await page.locator("#count").textContent(), "1");
    await page
      .locator(".chip")
      .filter({ hasText: "География не уточнена" })
      .click();
    assert.equal(await page.locator("#count").textContent(), "1");
    await page
      .locator(".chip")
      .filter({ hasText: "Международный охват" })
      .click();
    assert.equal(await page.locator("#count").textContent(), "1");
    await page.locator(".reset-btn").click();
    assert.equal(await page.locator("#count").textContent(), "5");
    if (process.env.ATLAS_TEST_FILE === "1") {
      const filePage = await browser.newPage();
      filePage.on("pageerror", (e) => errors.push(e.message));
      await filePage.goto("file://" + path.join(root, "index.html"));
      await filePage.waitForFunction(
        () => document.querySelector("#count").textContent === "779",
      );
      await filePage.close();
    }
    assert.deepEqual(errors, []);
    console.log(
      "PASS: demo, all-channel map, search, category/topic/region filters, grouping, sort, pagination, empty state, local correction persistence/reset, canvas click, mobile, API without geography; no JS errors.",
    );
  } finally {
    await browser.close();
  }
})()
  .catch((e) => {
    console.error(e);
    process.exitCode = 1;
  })
  .finally(() => server.close());
