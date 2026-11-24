/* node tests/layout-comparison.cjs <comparison directory> */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');
const folder = path.resolve(process.argv[2]);
const root = path.resolve(__dirname, '..');
const context = { window: {}, localStorage: { getItem: () => null }, console };
vm.createContext(context);
for (const name of ['taxonomy', 'topics', 'classification-v11-taxonomy', 'classification-v11-runtime', 'layout-policy', 'layout'])
  vm.runInContext(fs.readFileSync(path.join(root, 'web', name+'.js'), 'utf8'), context);
const rows = JSON.parse(fs.readFileSync(path.join(folder, 'snapshot.json'), 'utf8'));
const methods=Object.keys(JSON.parse(fs.readFileSync(path.join(folder,'comparison.json'),'utf8')).methods);
const enriched = rows.map(r => ({...context.window.Topics.enrich(r), layout_source_binding:context.window.Layout.rowBinding(r)}));
for (const method of methods) {
  const artifact = JSON.parse(fs.readFileSync(path.join(folder, method+'.json'), 'utf8'));
  assert(context.window.Layout.fromArtifact(enriched, artifact), method+' must accept raw→enriched snapshot');
  const changed = enriched.map(r => ({...r}));
  changed[0].subcategory = 'other';
  assert.equal(context.window.Layout.fromArtifact(changed, artifact), null);
}
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      let ctor;
      window.__atlasMaps=[];
      Object.defineProperty(window,'CanvasMap',{configurable:true,get:()=>ctor,set:Base=>{
        ctor=class extends Base{constructor(...args){super(...args);window.__atlasMaps.push(this);}};
      }});
    });
    await page.goto(pathToFileURL(path.join(folder, 'comparison.html')).href);
    await page.waitForSelector('canvas');
    assert.equal(await page.locator('canvas').count(), methods.length);
    await page.locator('#search').fill(rows[0].username);
    await page.locator('#glow').uncheck();
    await page.locator('#reset').click();
    const coordinates=await page.evaluate(()=>JSON.stringify(window.__atlasMaps[0].layout.nodes.map(n=>[n.id,n.x,n.y])));
    await page.selectOption('#region','*');
    assert(await page.evaluate(()=>window.__atlasMaps[0].visibleIds.size>0));
    await page.selectOption('#region',{label:'Москва'});
    assert(await page.evaluate(()=>{const m=window.__atlasMaps[0];return m.layout.nodes.filter(n=>m.visibleIds.has(n.id)).every(n=>n.layout_geography.regions.includes('Москва'));}));
    assert.equal(await page.evaluate(()=>JSON.stringify(window.__atlasMaps[0].layout.nodes.map(n=>[n.id,n.x,n.y]))),coordinates);
    await page.locator('#reset').click();
    await page.locator('button').filter({hasText:'Вписать'}).first().click();
    if (process.env.ATLAS_SCREENSHOT) await page.screenshot({path:process.env.ATLAS_SCREENSHOT,fullPage:true});
    if (process.env.ATLAS_SCREENSHOT) {
      await page.evaluate(()=>{
        const m=window.__atlasMaps[0],a=m.layout.annotations?.find(a=>a.kind==='region'&&a.label==='Москва');
        if(!a)return;
        const r=m.wrap.getBoundingClientRect();m.view.scale=m._overviewScale*2;
        m.view.x=r.width*.5-a.x*m.view.scale;m.view.y=r.height*.5-a.y*m.view.scale;m._draw();
      });
      await page.screenshot({path:process.env.ATLAS_SCREENSHOT.replace(/\.png$/,'.regions.png'),fullPage:true});
    }
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
    // Exercise the real application without writing a public layout artifact.
    const main = await browser.newPage();
    main.on('pageerror', e => errors.push(e.message));
    await main.route('https://atlas.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/data/channels.json') return route.fulfill({json:{channels:rows}});
      if (url.pathname === '/data/atlas_layout.json') return route.fulfill({contentType:'application/json',body:fs.readFileSync(path.join(folder,'tsne.json'),'utf8')});
      const file = path.join(root,'web',url.pathname === '/'?'index.html':url.pathname.slice(1));
      if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
      return route.fulfill({contentType:({'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
    });
    await main.goto('https://atlas.test/');
    await main.waitForFunction(() => document.getElementById('layout-method')?.textContent.startsWith('TSNE'));
    await main.selectOption('#group-by','region');
    assert((await main.locator('#layout-method').textContent()).includes('Локальные каналы'));
    await main.selectOption('#group-by','topic');
    assert((await main.locator('#layout-method').textContent()).startsWith('TSNE'));
    assert.deepEqual(errors, []);
    console.log('PASS: both real artifacts accepted; stale topics rejected; comparison UI and mobile; real application uses t-SNE and switches back to region scheme without publishing files.');
  } finally { await browser.close(); }
})().catch(e => {console.error(e);process.exitCode=1;});
