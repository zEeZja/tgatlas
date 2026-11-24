/* Demo/API contracts and responsive drawers, independent of owner exports. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../web');
const server=http.createServer((req,res)=>{
 const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname),file=path.resolve(root,name==='/'?'index.html':'.'+name);
 if(!file.startsWith(root+path.sep)){res.writeHead(403);res.end();return;}
 fs.readFile(file,(err,data)=>{if(err){res.writeHead(404);res.end();return;}res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.json':'application/json','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');res.end(data);});
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/data/channels.json',route=>route.fulfill({status:404}));
  await page.route('**/data/atlas_layout.json',route=>route.fulfill({status:404}));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>document.getElementById('count').textContent==='779');
  assert((await page.title()).startsWith('TG ATLAS'));
  await page.locator('.logo img').evaluate(img=>img.decode());
  if(process.env.ATLAS_REPO_SCREENSHOT){await page.evaluate(()=>new Promise(requestAnimationFrame));await page.screenshot({path:process.env.ATLAS_REPO_SCREENSHOT});}
  // Demo must be disclosed even though operational classifier banners are hidden.
  assert((await page.locator('#mode-description').innerText()).includes('Демо'));
  await page.keyboard.press('/');assert(await page.locator('#search').evaluate(e=>e===document.activeElement));
  assert.equal(await page.locator('.result').count(),80);await page.locator('.load-more').click();assert.equal(await page.locator('.result').count(),180);
  await page.locator('#search').fill('@breakingmash');await page.waitForFunction(()=>document.querySelectorAll('.result').length===1);
  await page.locator('.result').click();assert(await page.locator('#detail').isVisible());
  await page.keyboard.press('Escape');await page.locator('#search-toggle').click();await page.locator('#clear').click();await page.keyboard.press('Escape');
  for(const [width,height]of [[1440,1000],[1280,720],[1024,768],[390,844],[320,568]]){
   await page.setViewportSize({width,height});
   await page.locator('#search-toggle').click();
   assert((await page.locator('#search').boundingBox()).width>=240);
   assert((await page.locator('#list').boundingBox()).height>100);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.keyboard.press('Escape');
   await page.locator('#filters-toggle').click();assert(await page.locator('#filter-panel').isVisible());
   await page.locator('#advanced-filters>summary').click();assert(await page.locator('#publication-format').isVisible());
   await page.locator('#advanced-filters>summary').click();await page.keyboard.press('Escape');
  }
  await page.route('**/api.js*',route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(root,'api.js'),'utf8')+"\nApi.CONFIG.mode='http';Api.CONFIG.httpBase='';"}));
  await page.route('**/api/channels',route=>route.fulfill({json:{channels:[{id:'science',username:'science_channel',name:'Новости науки',description:'Научные открытия и исследования',subcategory:'science',country:'DE'}]}}));
  await page.reload();await page.waitForFunction(()=>document.getElementById('count').textContent==='1');
  await page.locator('#search-toggle').click();await page.locator('.result').click();
  assert((await page.locator('.detail-badges').innerText()).includes('Наука'));
  for(const href of await page.locator('#detail a').evaluateAll(links=>links.map(a=>a.href)))assert(href.startsWith('https://t.me/'));
  assert.deepEqual(errors,[]);await page.close();
  console.log('PASS: demo disclosure, lazy search, keyboard, drawers at 5 viewport sizes, API contract and safe Telegram links.');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
