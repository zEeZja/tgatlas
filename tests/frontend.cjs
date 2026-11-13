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
    // Demo coverage must be independent of locally generated user exports.
    await page.route("**/data/channels.js", route => route.fulfill({contentType: "application/javascript", body: "// isolated test: no JS export"}));
    await page.route("**/data/channels.json", (route) =>
      route.fulfill({ status: 404 }),
    );
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
    const moscowCount = await page.evaluate(() => TG_DATA.channels.map(c => Topics.enrich(c)).filter(c => c.geography_regions.includes("Москва")).length);
    assert(moscowCount > 0);
    assert.equal(await page.locator("#count").textContent(), String(moscowCount));
    await page.locator("#group-by").selectOption("region");
    assert.equal(await page.locator("#count").textContent(), String(moscowCount));
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
      (await page.locator(".detail-badges").innerText()).includes(
        "Общая повестка",
      ),
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
      (await page.locator(".detail-badges").innerText()).includes(
        "Общая повестка",
      ),
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
    // The API cannot inject arbitrary schemes through a catalogue URL.
    await page.evaluate(() => {
      document.querySelectorAll("#detail a").forEach((a) => {
        if (!a.href.startsWith("https://t.me/"))
          throw Error("Unexpected channel link");
      });
    });
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
      .filter({ hasText: "Недостаточно данных о географии" })
      .click();
    assert.equal(await page.locator("#count").textContent(), "1");
    await page
      .locator(".chip")
      .filter({ hasText: "Международная повестка" })
      .click();
    assert.equal(await page.locator("#count").textContent(), "1");
    await page.locator(".reset-btn").click();
    assert.equal(await page.locator("#count").textContent(), "5");
    await page.setViewportSize({ width: 1440, height: 1000 });
    // V2: parent groups, exact subtopics, independent formats, optional tags.
    const v2Rows = [
      {
        id: "v21",
        username: "v21",
        name: "Программирование Python",
        description: "Программирование",
        country: "RU",
      },
      { id: "v22", username: "v22", name: "Новости кибербезопасности" },
      { id: "v23", username: "v23", name: "Программирование и криптовалюты" },
      { id: "v24", username: "v24", name: "Личные финансы" },
    ].map((c) => ({ ...c, url: "https://t.me/" + c.username }));
    await page.unroute("**/api/channels");
    await page.route("**/api/channels", (route) =>
      route.fulfill({ json: { channels: v2Rows } }),
    );
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#count").textContent === "4",
    );
    await page.locator("#category").selectOption("group:technology");
    assert.equal(await page.locator("#count").textContent(), "2");
    await page.locator("#include-secondary").check();
    assert.equal(await page.locator("#count").textContent(), "3");
    await page.locator("#publication-format").selectOption("news");
    assert.equal(await page.locator("#count").textContent(), "1");
    await page.locator(".result").click();
    assert(
      (await page.locator(".detail-badges").innerText()).includes(
        "Кибербезопасность",
      ),
    );
    await page.keyboard.press("Escape");
    await page.locator(".reset-btn").click();
    await page.locator("#category").selectOption("programming");
    assert.equal(await page.locator("#count").textContent(), "1");
    assert(await page.locator(".geo-not-applicable").isVisible());
    assert.equal(await page.locator("#count").textContent(), "1");
    await page.locator(".result").click();
    assert(
      !(await page.locator(".detail-badges").innerText()).includes(
        "Регион не определён",
      ),
    );
    await page.keyboard.press("Escape");
    await page.locator(".reset-btn").click();
    // Sector subjects are nonterritorial even when an old row carries a region.
    const sectors = [
      {id: "sector1", username: "sector1", name: "Кредитование", region: "Москва", country: "RU"},
      {id: "sector2", username: "sector2", name: "Финансово-банковская сфера"},
      {id: "sector3", username: "sector3", name: "Авиастроение"},
      {id: "sector4", username: "sector4", name: "Авиатранспорт"},
      {id: "sector5", username: "sector5", name: "ЧП Казань"},
    ];
    await page.unroute("**/api/channels");
    await page.route("**/api/channels", route => route.fulfill({json: {channels: sectors}}));
    await page.reload();
    await page.waitForFunction(() => document.querySelector("#count").textContent === "5");
    await page.locator("#category").selectOption("banking");
    assert.equal(await page.locator("#count").textContent(), "2");
    assert(await page.locator(".geo-not-applicable").isVisible());
    await page.locator(".result").first().click();
    assert((await page.locator(".geography-explanation").innerText()).includes("не применяется"));
    await page.keyboard.press("Escape");
    await page.locator(".reset-btn").click();
    await page.locator("#category").selectOption("industry");
    assert.equal(await page.locator("#count").textContent(), "1");
    await page.locator(".reset-btn").click();
    if (process.env.ATLAS_TEST_FILE === "1") {
      const filePage = await browser.newPage();
      filePage.on("pageerror", (e) => errors.push(e.message));
      await filePage.goto("file://" + path.join(root, "index.html"));
      await filePage.waitForFunction(
        () => document.querySelector("#count").textContent === "779",
      );
      await filePage.close();
    }
    // A JSON-only live export takes priority over demo, including its audience.
    const live = await browser.newPage();
    await live.addInitScript(() => {
      window.TG_DATA = {channels: [{username: "old_snapshot", name: "Старый JS-снимок"}]};
    });
    await live.route("**/data/channels.js", route => route.fulfill({contentType: "application/javascript", body: "// JSON-only fixture"}));
    const liveRequests = [];
    live.on("request", (r) => liveRequests.push(r.url()));
    await live.route("**/data/channels.json", (route) =>
      route.fulfill({
        json: {
          _sample: false,
          channels: [{ ...channel, url: "javascript:alert(1)", last_meta_at: new Date().toISOString(), last_meta_attempt_at: new Date().toISOString(), last_meta_error: "timeout" }],
        },
      }),
    );
    await live.goto(url);
    await live.waitForFunction(
      () => document.querySelector("#count").textContent === "1",
    );
    assert(!liveRequests.some((u) => u.endsWith("/demo.js")));
    assert(!liveRequests.some((u) => u.endsWith("/data/channels.js")));
    assert(!(await live.locator("#banner").isVisible()));
    assert((await live.locator(".freshness-summary").innerText()).includes("1 свежих"));
    await live.locator(".result").click();
    assert.equal(await live.locator(".metadata-freshness").getAttribute("data-status"), "fresh");
    assert((await live.locator(".metadata-error").innerText()).includes("Прежние данные сохранены"));
    assert.equal(
      await live.locator(".detail-open").getAttribute("href"),
      "https://t.me/api",
    );
    for (const response of [
      { status: 500, body: "failed" },
      { status: 200, contentType: "application/json", body: "{}" },
      { status: 200, json: {channels: [channel, {...channel, username: channel.username.toUpperCase()}]} },
    ]) {
      await live.unroute("**/data/channels.json");
      await live.route("**/data/channels.json", (route) =>
        route.fulfill(response),
      );
      await live.reload();
      await live.locator(".app-message").waitFor();
      assert(!liveRequests.some((u) => u.endsWith("/demo.js")));
    }
    await live.close();
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
