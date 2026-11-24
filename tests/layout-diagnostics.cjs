/* Deterministic rejection reasons; never read or modify working map data. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const c={window:{},localStorage:{getItem:()=>null},console};vm.createContext(c);
for(const name of ['taxonomy','topics','layout-policy','layout'])vm.runInContext(fs.readFileSync(path.join(root,'web',name+'.js'),'utf8'),c);
const api=c.window.Layout;
const rows=['alpha_channel','bravo_channel','charlie_channel'].map((username,i)=>c.window.Topics.enrich({id:username,username,name:`Канал ${i}`,description:'',subcategory:'other'}));
const layout=api.buildClusterLayout(rows);
for(const n of layout.nodes){const row=rows.find(r=>r.id===n.id);n.topic=row.subcategory;n.group=row.topic_group||'';n.layout_geography=api.geographyRole(row);}
const artifact={format:'atlas-semantic-layout-v1',method:'tsne',created_at:new Date().toISOString(),policy_sha256:c.window.TG_MAP_POLICY.fingerprint,layout,bindings:Object.fromEntries(rows.map(r=>[r.id,api.rowBinding(r)]))};
assert.equal(api.inspectArtifact(rows,artifact).code,'ok');
assert(api.fromArtifact(rows,artifact));
function check(code,modify){const rr=JSON.parse(JSON.stringify(rows)),aa=JSON.parse(JSON.stringify(artifact));modify(rr,aa);const result=api.inspectArtifact(rr,aa);assert.equal(result.code,code);assert.equal(result.layout,null);assert(result.reason.length>0);assert.equal(api.fromArtifact(rr,aa),null);}
check('policy_mismatch',(_,a)=>a.policy_sha256='different');
check('channel_count',(_,a)=>a.layout.nodes.pop());
check('expired_artifact',(_,a)=>a.created_at=new Date(Date.now()-8*86400000).toISOString());
check('future_artifact',(_,a)=>a.created_at=new Date(Date.now()+3600000).toISOString());
check('local_correction',r=>r[0].topic_source='local');
check('binding_mismatch',r=>r[0].name+=' changed');
check('topic_mismatch',(_,a)=>a.layout.nodes[0].topic='different');
check('geography_mismatch',(_,a)=>a.layout.nodes[0].layout_geography.key='different');
check('invalid_coordinates',(_,a)=>a.layout.nodes[0].x=-1);
assert.equal(api.inspectArtifact(rows,null).code,'missing_artifact');
assert.equal(api.inspectArtifact(rows,artifact,'region').code,'region_mode');
console.log('PASS: t-SNE diagnostics distinguish local edits, stale data, policy/date/count and coordinate errors.');
