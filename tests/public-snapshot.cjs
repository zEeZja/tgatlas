/* Source priority, source isolation, integrity and classification parity. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../web');
const payload=JSON.parse(fs.readFileSync(path.join(root,'snapshot/channels.json'),'utf8'));
const artifact=JSON.parse(fs.readFileSync(path.join(root,'snapshot/layout.json'),'utf8'));
const manifest=JSON.parse(fs.readFileSync(path.join(root,'snapshot/manifest.json'),'utf8'));
for(const [name,info] of Object.entries(manifest.files))assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'snapshot',name))).digest('hex'),info.sha256);
async function apiCase(files){
 const requests=[],ctx={window:{},location:{protocol:'http:'},fetch:async url=>{requests.push(url);const v=files[url];return{status:v===undefined?404:200,ok:v!==undefined,json:async()=>v};}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(root,'api.js'),'utf8'),ctx);
 const rows=await ctx.window.Api.getChannels(),layout=await ctx.window.Api.getLayout();
 return{rows,layout,requests,config:ctx.window.Api.CONFIG};
}
(async()=>{
 let result=await apiCase({'./snapshot/channels.json':payload,'./snapshot/layout.json':artifact,'./data/atlas_layout.json':{wrong:true}});
 assert.equal(result.rows.length,manifest.channels);assert.equal(result.layout._snapshot_id,manifest.id);
 assert(!result.requests.includes('./data/atlas_layout.json'));
 result=await apiCase({'./data/channels.json':{channels:[{username:'local',name:'Локальный'}]},'./snapshot/layout.json':artifact});
 assert.equal(result.rows[0].username,'local');assert.equal(result.layout,null);assert.equal(result.config.snapshotReference,null);
 assert(!result.requests.some(p=>p.includes('/snapshot/')));
 result=await apiCase({'./snapshot/channels.json':payload,'./snapshot/layout.json':{...artifact,_snapshot_id:'wrong'}});
 assert.equal(result.layout,null);
 const c={window:{Api:{CONFIG:{snapshotReference:Date.parse(manifest.reference_at)}}},localStorage:{getItem:()=>null},console};vm.createContext(c);
 for(const name of ['taxonomy','topics','classification-v11-taxonomy','classification-v11-runtime'])vm.runInContext(fs.readFileSync(path.join(root,name+'.js'),'utf8'),c);
 const working=path.join(root,'data/channels.json');
 if(fs.existsSync(working)){
   const original=JSON.parse(fs.readFileSync(working,'utf8')).channels;
   assert.equal(original.length,payload.channels.length);
   const keys=['id','username','subcategory','topic_group','topic_granularity','topic_source','geography_scope','geography_country','geography_regions'];
   const core=row=>Object.fromEntries(keys.map(k=>[k,row[k]]));
   for(let i=0;i<original.length;i++)assert.deepEqual(core(c.window.Topics.enrich(original[i])),core(c.window.Topics.enrich(payload.channels[i])));
   assert.equal(crypto.createHash('sha256').update(fs.readFileSync(working)).digest('hex'),manifest.source_channels_sha256);
 }
 const text=JSON.stringify(payload);
 for(const forbidden of ['displayed_posts','topic_diagnostics','topic_evidence','geography_evidence','classification_input_profile'])assert(!text.includes('"'+forbidden+'":'));
 console.log('PASS: snapshot integrity, privacy, source priority/isolation and local classification parity.');
})().catch(e=>{console.error(e);process.exitCode=1;});
