/* Synthetic by default; opt in to read-only published-data checks locally. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../web');
const fixture=process.env.ATLAS_USE_PUBLISHED_DATA==='1'?null:require('./fixtures/product-catalog.cjs')(Number(process.env.ATLAS_TEST_CHANNELS||1200));
const artifact=fixture?fixture.artifact:JSON.parse(fs.readFileSync(path.join(root,'data/atlas_layout.json'),'utf8'));
const server=http.createServer((req,res)=>{
 const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname),file=path.resolve(root,name==='/'?'index.html':'.'+name);
 if(fixture&&['/data/channels.json','/data/atlas_layout.json'].includes(name)){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(name.includes('channels.json')?{channels:fixture.rows}:artifact));return;}
 if(!file.startsWith(root+path.sep)){res.writeHead(403);res.end();return;}
 fs.readFile(file,(err,data)=>{if(err){res.writeHead(404);res.end();return;}res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.json':'application/json','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');res.end(data);});
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:3});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{let ctor;Object.defineProperty(window,'CanvasMap',{get:()=>ctor,set:Base=>{ctor=class extends Base{constructor(...args){super(...args);window.__map=this;}};}});});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>window.__map?.layout.nodes.length>1000);
  const screenshots=process.env.ATLAS_UI_SCREENSHOTS;
  if(screenshots){fs.mkdirSync(screenshots,{recursive:true});await page.screenshot({path:path.join(screenshots,'desktop.png')});}
  assert(await page.locator('#search-panel').isHidden());assert(await page.locator('#filter-panel').isHidden());
  assert.equal(await page.locator('.result').count(),0,'Closed search must not build 200 hidden results');
  assert.equal(await page.locator('.result-arrow').count(),0);
  assert(!/11v2|TSNE|семантический снимок|для проверки темы/.test(await page.locator('body').innerText()));
  assert.equal(Math.round((await page.locator('#map-wrap').boundingBox()).width),1440);
  assert(await page.evaluate(()=>document.querySelector('#map').width<=innerWidth*2));
  await page.locator('#filters-toggle').click();assert(await page.locator('#filter-panel').isVisible());
  if(screenshots)await page.screenshot({path:path.join(screenshots,'filters.png')});
  assert(await page.locator('#territory-section').isHidden());
  await page.selectOption('#category','group:v11_finance');
  assert((await page.locator('#filters-badge').textContent())==='1');
  await page.locator('#search-toggle').click();assert(await page.locator('#filter-panel').isHidden());
  assert(await page.locator('#search-panel').isVisible());assert((await page.locator('.result').count())<=80);
  if(screenshots)await page.screenshot({path:path.join(screenshots,'search.png')});
  await page.locator('#search').fill('readovkanews');
  await page.waitForTimeout(200);
  await page.keyboard.press('Escape');assert(await page.locator('#search-panel').isHidden());
  await page.locator('#filters-toggle').click();await page.locator('.reset-btn').click();await page.locator('#apply-filters').click();
  await page.locator('[data-view="region"]').click();
  if(screenshots)await page.screenshot({path:path.join(screenshots,'geography.png')});
  assert(await page.evaluate(()=>window.__map.layout.nodes.length>0&&window.__map.layout.nodes.every(window.AtlasView.geographicChannel)));
  assert(await page.evaluate(()=>window.__map.layout.clusters.every(c=>!c.id.startsWith('context:'))));
  await page.locator('#filters-toggle').click();assert(await page.locator('#territory-section').isVisible());
  await page.keyboard.press('Escape');
  await page.locator('[data-view="topic"]').click();
  assert.equal(await page.evaluate(()=>window.__map.layout.nodes.length),artifact.layout.nodes.length);
  assert(await page.evaluate(()=>window.__map.layout.annotations.every(a=>!['country','region'].includes(a.kind))));
  const coordinates=await page.evaluate(()=>window.__map.layout.nodes.map(n=>[n.id,n.x,n.y]));
  const name=artifact.layout.nodes[0].username;
  await page.evaluate(name=>window.Topics.setLocal(name,'v11_medicine'),name);await page.reload();
  await page.waitForFunction(()=>document.querySelector('#layout-method').dataset.layoutReason==='local_overlay');
  assert.deepEqual(await page.evaluate(()=>window.__map.layout.nodes.map(n=>[n.id,n.x,n.y])),coordinates);
  await page.setViewportSize({width:390,height:844});
  if(screenshots)await page.screenshot({path:path.join(screenshots,'mobile.png')});
  await page.locator('#search-toggle').click();await page.locator('#search').fill(name);
  await page.waitForFunction(()=>document.querySelectorAll('.result').length===1);await page.locator('.result').click();
  assert(await page.locator('#search-panel').isHidden());assert(await page.locator('#detail').isVisible());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('.detail-close').click();
  if(process.env.ATLAS_MOBILE_SCREENSHOT)await page.screenshot({path:process.env.ATLAS_MOBILE_SCREENSHOT});
  assert.deepEqual(errors,[]);
  await page.close();console.log('PASS: product shell, drawers, lazy results, filtering, geographic isolation, local corrections, DPR cap and mobile with no JS errors.');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
