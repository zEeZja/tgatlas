/* Main app contract with 11v2 topic/group/abstain exports; no live data writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const rows=JSON.parse(execFileSync(process.env.PYTHON||'python',['-B','-c',"import json; from tests.test_v11v2_publication import fixture; from src.classification_v11v2_runtime import public_result; rows,data=fixture(); print(json.dumps([public_result(data['channels'][r['username']],data)|r for r in rows]))"],{cwd:root,encoding:'utf8'}));
if(process.env.ATLAS_TEST_CHANNELS){
 const size=Number(process.env.ATLAS_TEST_CHANNELS);assert(Number.isInteger(size)&&size>=3&&size<=6000);
 const prototypes=rows.slice();
 while(rows.length<size){const i=rows.length,base=prototypes[i%prototypes.length],name=`Load channel ${i}`;rows.push({...base,id:`load_${i}`,username:`load_${i}`,name,classification_profile:{...base.classification_profile,name}});}
}
const c={window:{},localStorage:{getItem:()=>null},console};vm.createContext(c);
for(const name of ['taxonomy','topics','classification-v11-taxonomy','classification-v11-runtime','layout-policy','layout'])vm.runInContext(fs.readFileSync(path.join(root,'web',name+'.js'),'utf8'),c);
const enriched=rows.map(r=>c.window.Topics.enrich(r));
const layout=c.window.Layout.buildClusterLayout(enriched);
for(const node of layout.nodes){const row=rows.find(r=>r.id===node.id);node.topic=row.subcategory;node.group=row.topic_group;}
// Synthetic coordinates test artifact loading, not t-SNE quality.
const artifact={format:'atlas-semantic-layout-v1',method:'tsne',created_at:new Date().toISOString(),policy_sha256:c.window.TG_MAP_POLICY.fingerprint,
 bindings:Object.fromEntries(rows.map(r=>[r.id,c.window.Layout.rowBinding(r)])),evidence:{with_posts:0},layout};
const tempRoot=process.env.OPENCODE_TEMP||process.env.TMPDIR||require('node:os').tmpdir();
const scratch=fs.mkdtempSync(path.join(tempRoot,'v11v2-map-'));
fs.cpSync(path.join(root,'web'),scratch,{recursive:true,filter:source=>!source.startsWith(path.join(root,'web/data'))});
fs.mkdirSync(path.join(scratch,'data'));
fs.writeFileSync(path.join(scratch,'data/channels.json'),JSON.stringify({channels:rows}));
fs.writeFileSync(path.join(scratch,'data/channels.js'),'window.TG_DATA='+JSON.stringify({channels:rows})+';');
fs.writeFileSync(path.join(scratch,'data/atlas_layout.json'),JSON.stringify(artifact));
fs.writeFileSync(path.join(scratch,'data/atlas_layout.js'),'window.TG_ATLAS_LAYOUT='+JSON.stringify(artifact)+';');
const server=http.createServer((req,res)=>{
 const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
 const file=path.resolve(scratch,name==='/'?'index.html':'.'+name);
 if(!file.startsWith(scratch+path.sep)){res.writeHead(403);res.end();return;}
 fs.readFile(file,(error,data)=>{if(error){res.writeHead(404);res.end();return;}res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.json':'application/json','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');res.end(data);});
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH});
 try{
  for(const url of [`http://127.0.0.1:${server.address().port}/`,pathToFileURL(path.join(scratch,'index.html')).href]){
   const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.goto(url);
   await page.waitForFunction(()=>document.getElementById('layout-method')?.textContent.startsWith('TSNE'));
    assert(!(await page.locator('body').innerText()).includes('11v2'));
    await page.locator('#search-toggle').click();
   await page.locator('#search').fill('second');
   await page.waitForFunction(()=>document.querySelectorAll('.result').length===1);
   assert((await page.locator('.result').innerText()).includes('без уточнения подтемы'));
   await page.locator('.result').click();
   assert((await page.locator('.detail-badges').innerText()).includes('Бизнес'));
    await page.locator('.detail-close').click();
    await page.locator('#search-toggle').click();
   await page.locator('#search').fill('third');
   await page.waitForFunction(()=>document.querySelector('.result')?.textContent.includes('third'));
   assert((await page.locator('.result').innerText()).includes('Тема не определена'));
   await page.locator('.result').click();
   await page.locator('details.topic-edit').evaluate(e=>e.open=true);
   await page.selectOption('#topic-correction','v11_medicine');
   await page.locator('#save-topic').click();
   assert((await page.locator('.detail-badges').innerText()).includes('Медицина'));
    assert((await page.locator('#layout-method').textContent()).startsWith('TSNE'));
    assert.equal(await page.locator('#layout-method').getAttribute('data-layout-reason'),'local_overlay');
   await page.selectOption('#topic-correction','');await page.locator('#save-topic').click();
   assert((await page.locator('.detail-badges').innerText()).includes('Тема не определена'));
    assert((await page.locator('#layout-method').textContent()).startsWith('TSNE'));
    assert.equal(await page.locator('#layout-method').getAttribute('data-layout-reason'),'ok');
   await page.setViewportSize({width:390,height:844});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.deepEqual(errors,[]);await page.close();
  }
   console.log(`PASS: ${rows.length} channels; 11v2 topic/group/abstain in real app over HTTP and file://; t-SNE loading, local correction/reset and mobile.`);
 }finally{await browser.close();server.close();fs.rmSync(scratch,{recursive:true,force:true,maxRetries:5,retryDelay:100});}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
