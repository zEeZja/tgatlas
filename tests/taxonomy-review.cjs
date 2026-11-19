/* Local draft UI, separate from the active taxonomy and V10 viewer. */
const {chromium} = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const {pathToFileURL} = require("node:url");
const {spawnSync} = require("node:child_process");

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "atlas-taxonomy-"));
  const html = path.join(temp, "draft.html");
  const generated = spawnSync("python", ["-B", "-c", `
import sys
from src.taxonomy_experiment import load_taxonomy, render_review
row = dict(username='demo', name='Канал <script>bad()</script>', description='Научные исследования', current='science', v10='tech', status='posts_available', low_diversity=True, fetched_at='2026-11-19', posts=[dict(text='Текст </script><script>bad()</script>',message_id=1,date='2026-11-19')], profile_candidates=[dict(id='software_development',group='technology',score=.8)], post_candidates=[dict(id='aviation',group='industry_transport',score=.9)], combined_candidates=[dict(id='aviation',group='industry_transport',score=.9)])
analysis = dict(channels=[row], clusters=[dict(id=0,channels=['demo'],nearest_draft_topics=[dict(id='aviation',group='industry_transport',score=.9)])], noise_channels=0, summary=dict(with_posts=1), taxonomy_sha256='test')
open(sys.argv[1], 'w', encoding='utf-8').write(render_review(load_taxonomy(),analysis))
`, html], {cwd:path.resolve(__dirname, ".."), encoding:"utf8"});
  assert.equal(generated.status, 0, generated.stderr);
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:["--no-sandbox"]});
  try {
    const page = await browser.newPage(); const errors=[], requests=[];
    page.on("pageerror", (e)=>errors.push(e.message));
    page.on("request", (r)=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
    await page.goto(pathToFileURL(html).href);
    assert.equal(await page.locator("#taxonomy > .card").count(), 18);
    assert((await page.locator("#stats").innerText()).includes("Подтем: 66"));
    await page.locator("#group").selectOption("industry_transport");
    assert.equal(await page.locator("#taxonomy > .card").count(), 1);
    await page.locator("#taxonomy .example").click();
    assert(await page.locator("#channels").isVisible());
    assert.equal(await page.locator("#channel-list .card").count(), 1);
    assert((await page.locator("#channel-list h2").innerText()).includes("<script>"));
    assert.equal(await page.locator("#channel-list script").count(), 0);
    assert((await page.locator(".compare").innerText()).includes("Авиация"));
    await page.locator("summary").click();
    assert((await page.locator(".post p").innerText()).includes("<script>"));
    await page.locator("#mode").selectOption("changed");
    assert.equal(await page.locator("#channel-list .card").count(), 1);
    await page.locator("#mode").selectOption("missing");
    assert.equal(await page.locator("#channel-list .card").count(), 0);
    await page.locator('[data-tab="clusters"]').click();
    assert.equal(await page.locator("#clusters .card").count(), 1);
    await page.locator("#clusters .example").click();
    assert.equal(await page.locator("#channel-list .card").count(), 1);
    await page.locator("#search").fill("");
    await page.locator('[data-tab="migration"]').click();
    assert((await page.locator("#migration").innerText()).includes("Не переносится"));
    await page.locator('[data-tab="taxonomy"]').click();
    await page.locator("#search").fill("ипотека");
    assert.equal(await page.locator("#taxonomy > .card").count(), 1);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    assert.deepEqual(errors,[]); assert.deepEqual(requests,[]);
    console.log("PASS: draft taxonomy, post comparison, groups, migration, safe text, mobile, no network.");
  } finally {
    await browser.close();fs.rmSync(temp,{recursive:true,force:true});
  }
})().catch((error)=>{console.error(error);process.exitCode=1;});
