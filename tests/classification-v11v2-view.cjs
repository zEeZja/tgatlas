/* node tests/classification-v11v2-view.cjs <path to review.html> */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
const file=path.resolve(process.argv[2]);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],network=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url()))network.push(r.url());});
  await page.goto(pathToFileURL(file).href);
  assert((await page.title()).includes('11v2'));
  const cases=[['abiregbel','Бизнес, работа и продвижение · только раздел'],['dolgopacity','Общая новостная повестка'],['eduardinvest','Криптовалютный рынок'],['rybar','Военная тематика'],['bankuralsibnews','Банки и платежи']];
  const input=await page.locator('#view-data').textContent();
  const data=JSON.parse(input);
  const refined=data.rows.some(r=>r.diagnostics?.group_candidates);
  if(refined)cases.push(['dmigolub','Макроэкономика и рынки']);
  for(const [username,label] of cases){
   await page.locator('#search').fill(username);
   const card=page.locator(`article[data-username="${username}"]`);
   assert.equal(await card.count(),1);
   assert.equal(await card.locator('.predicted strong').textContent(),label);
   const diagnostics=card.locator('details').last();
   await diagnostics.locator('summary').click();
   assert((await diagnostics.textContent()).includes('Вес профиля'));
  }
  for(const username of ['findir_pro','gin_bataisk']){
   if(!refined||!data.rows.some(r=>r.username===username))continue;
   await page.locator('#search').fill(username);
   const card=page.locator(`article[data-username="${username}"]`);
   if(username==='findir_pro')assert(['group:v11_business_work','v11_management','v11_accounting_taxes'].includes(data.rows.find(r=>r.username===username).predicted));
   if(username==='gin_bataisk'){
    assert((await card.textContent()).includes('Ростовская область'));
    assert(!(await card.textContent()).includes('Забайкальский край:'));
   }
  }
  await page.locator('#search').fill('bankuralsibnews');
  await page.getByLabel('Моя оценка @bankuralsibnews',{exact:true}).selectOption('correct');
  await page.getByLabel('Оценка географии @bankuralsibnews',{exact:true}).selectOption('unsure');
  await page.getByLabel('Комментарий к географии @bankuralsibnews',{exact:true}).fill('Нужна отдельная проверка направленности');
  const downloaded=page.waitForEvent('download');
  await page.locator('#export').click();
  const download=await downloaded;
  const payload=JSON.parse(fs.readFileSync(await download.path(),'utf8'));
  assert.equal(payload.format,'atlas-v11v2-view-reviews-v1');
  assert.equal(payload.reviews.bankuralsibnews.verdict,'correct');
  assert.equal(payload.reviews.bankuralsibnews.geography_verdict,'unsure');
  await page.locator('#import').setInputFiles({name:'old.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...payload,format:'atlas-v11-view-reviews-v1'}))});
  await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Не удалось загрузить'));
  await page.locator('#import').setInputFiles({name:'new.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(payload))});
  await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Оценки загружены'));
  if(process.env.ATLAS_V11V2_SCREENSHOT)await page.screenshot({path:process.env.ATLAS_V11V2_SCREENSHOT,fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(network,[]);
  assert.deepEqual(errors,[]);
  console.log('PASS: 11v2 known cases, financial section and Bataysk, diagnostics, isolated reviews, offline and mobile without JS errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
