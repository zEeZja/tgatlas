/* Branding and responsive navigation. Uses demo data, never owner exports. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const root = path.resolve(__dirname, "../web");
const server = http.createServer((req, res) => {
  let file = path.resolve(root, "." + decodeURIComponent(new URL(req.url, "http://localhost").pathname));
  if (!file.startsWith(root + path.sep) && file !== root) {res.writeHead(403); res.end(); return;}
  if (file === root) file = path.join(root, "index.html");
  fs.readFile(file, (error, content) => {
    if (error) {res.writeHead(404); res.end(); return;}
    res.setHeader("Content-Type", {".html":"text/html; charset=utf-8",".js":"application/javascript",".css":"text/css",".svg":"image/svg+xml"}[path.extname(file)] || "application/octet-stream");
    res.end(content);
  });
});
(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH, args:["--no-sandbox"]});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:"reduce"});
    const errors=[];
    page.on("pageerror",(error)=>errors.push(error.message));
    await page.route("**/data/channels.js",(route)=>route.fulfill({contentType:"application/javascript",body:"// no owner export"}));
    await page.route("**/data/channels.json",(route)=>route.fulfill({status:404}));
    await page.goto("http://127.0.0.1:"+server.address().port);
    await page.waitForFunction(()=>document.querySelector("#count").textContent==="779");
    assert((await page.title()).startsWith("АТЛАС"));
    assert.equal(await page.locator(".atlas-wordmark").evaluate((e)=>e.firstChild.textContent),"АТЛАС");
    await page.locator(".atlas-mark").evaluate((img)=>img.decode());
    assert.equal(await page.locator("#publication-format").isVisible(),false);
    await page.keyboard.press("/");
    assert(await page.locator("#search").evaluate((e)=>e===document.activeElement));
    await page.locator("#search").fill("@breakingmash");
    await page.waitForFunction(()=>document.querySelector("#count").textContent==="1");
    assert.equal(await page.locator(".search-shortcut").isVisible(),false);
    await page.locator(".reset-btn").click();
    await page.locator("#advanced-filters > summary").click();
    assert(await page.locator("#publication-format").isVisible());
    await page.locator("#publication-format").selectOption("news");
    assert.equal(await page.locator("#advanced-count").textContent(),"1");
    await page.locator("#advanced-filters > summary").click();
    assert(await page.locator("#advanced-count").isVisible());
    await page.locator(".reset-btn").click();
    assert.equal(await page.locator("#advanced-count").isVisible(),false);
    const foreign = await page.locator('#country').evaluate((select)=>[...select.options].find((o)=>o.value && o.value!=='RU')?.value);
    if(foreign) {
      await page.locator('#country').selectOption(foreign);
      assert.equal(await page.locator('#region-block').isVisible(),false);
      await page.locator('.reset-btn').click();
    }
    const artifacts=process.env.ATLAS_DESIGN_SCREENSHOTS;
    if(artifacts)fs.mkdirSync(artifacts,{recursive:true});
    for(const [width,height] of [[1440,1000],[1280,720],[1024,768],[390,844],[320,568]]) {
      await page.setViewportSize({width,height});
      await page.locator(".filter-panel").evaluate((e)=>{e.scrollTop=0;});
      await page.evaluate(()=>new Promise(requestAnimationFrame));
      const geometry=await page.evaluate(()=>{
        const box=(selector)=>document.querySelector(selector).getBoundingClientRect().toJSON();
        return {search:box("#search"),head:box(".head"),filters:box(".filter-panel"),results:box(".results"),resultHead:box(".results-head"),overflow:document.documentElement.scrollWidth>innerWidth};
      });
      assert(!geometry.overflow,`horizontal overflow at ${width}x${height}`);
      assert(geometry.search.height>=52,"search must never shrink");
      assert(geometry.search.width>=270,"search remains usable on small phones");
      assert(geometry.search.y>=geometry.head.bottom-1,"heading/search collision");
      assert(geometry.filters.y>=geometry.search.bottom,"search/filters collision");
      assert(geometry.resultHead.y>=geometry.filters.bottom-1,"filters/results collision");
      assert(geometry.results.height>=99,"channel list must retain usable height");
      if(artifacts && (width===1440 || width===390 || width===320))await page.screenshot({path:path.join(artifacts,`atlas-${width}x${height}.png`)});
    }
    // On short phones the panel scrolls naturally; search stays accessible.
    await page.locator('#territory-filters').evaluate((e)=>{e.open=true;});
    await page.locator('#country').scrollIntoViewIfNeeded();
    assert(await page.locator('#search').evaluate((e)=>e.getBoundingClientRect().top>=document.querySelector('.topbar').getBoundingClientRect().bottom-1));
    await page.setViewportSize({width:390,height:844});
    await page.locator("#mobile-toggle").click();
    assert(await page.locator(".app").evaluate((e)=>e.classList.contains("map-mode")));
    await page.locator("#mobile-toggle").click();
    assert.deepEqual(errors,[]);
    console.log("PASS: АТЛАС identity, large search, grouped filters, advanced controls, keyboard, 5 viewport sizes, mobile map.");
  } finally {await browser.close();}
})().catch((error)=>{console.error(error);process.exitCode=1;}).finally(()=>server.close());
