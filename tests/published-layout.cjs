/* Browser regression for the actually published map, both HTTP and file://. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../web');
const bundled=!fs.existsSync(path.join(root,'data/channels.json'));
const artifact = JSON.parse(fs.readFileSync(path.join(root, bundled?'snapshot/layout.json':'data/atlas_layout.json'), 'utf8'));
const expected = artifact.layout.nodes.map(n => [n.id,n.x,n.y]);
assert.equal(artifact.method, 'tsne');
const server = http.createServer((req,res) => {
  const name = decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  const file = path.resolve(root, name==='/'?'index.html':'.'+name);
  if(!file.startsWith(root+path.sep)) {res.writeHead(403);res.end();return;}
  fs.readFile(file,(err,data)=>{
    if(err) {res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.json':'application/json','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');
    res.end(data);
  });
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH});
  try {
    for(const url of [`http://127.0.0.1:${server.address().port}/`,pathToFileURL(path.join(root,'index.html')).href]) {
      const page=await browser.newPage({viewport:{width:1440,height:1000}});
      const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
       await page.addInitScript(()=>{
        let ctor;
        Object.defineProperty(window,'CanvasMap',{configurable:true,get:()=>ctor,set:Base=>{
          ctor=class extends Base {constructor(...args){super(...args);window.__publishedMap=this;}};
        }});
       });
       if(bundled)await page.addInitScript(()=>{Date.now=()=>Date.parse('2030-01-01T00:00:00Z');});
      await page.goto(url);
      await page.waitForFunction(()=>document.getElementById('layout-method')?.textContent.startsWith('TSNE'));
      const coordinates=()=>page.evaluate(()=>window.__publishedMap.layout.nodes.map(n=>[n.id,n.x,n.y]));
       assert.deepEqual(await coordinates(),expected);
       if(bundled){
         assert.equal(await page.evaluate(()=>Api.CONFIG.snapshotInfo.channels),expected.length);
         assert((await page.locator('.diagnostics').innerText()).includes('Публичный снимок'));
       }
      assert.equal(await page.evaluate(()=>window.__publishedMap.layout.annotations.length),artifact.layout.annotations.filter(a=>!['region','country'].includes(a.kind)).length);
      assert(await page.locator('#search-panel').isHidden());
      await page.locator('#search-toggle').click();
      await page.locator('#search').fill(artifact.layout.nodes[0].username);
      await page.waitForFunction(()=>window.__publishedMap.visibleIds?.size<3000);
      assert.deepEqual(await coordinates(),expected,'Search must preserve approved coordinates');
      await page.locator('#search').fill('');
      await page.locator('[aria-label="Закрыть поиск"]').click();
      await page.locator('[data-view="region"]').click();
      assert.equal(await page.locator('#layout-method').getAttribute('data-layout-reason'),'geography_view');
      assert(await page.evaluate(()=>window.__publishedMap.layout.nodes.every(window.AtlasView.geographicChannel)));
      await page.locator('[data-view="topic"]').click();
      assert.deepEqual(await coordinates(),expected,'Returning from region mode must restore approved t-SNE');
      await page.locator('#fit-map').click();
      if(process.env.ATLAS_SCREENSHOT&&url.startsWith('http:'))await page.screenshot({path:process.env.ATLAS_SCREENSHOT,fullPage:true});
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('PASS: published t-SNE loads over HTTP and file://; exact approved coordinates, captions, search, grouping and mobile without JS errors.');
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
