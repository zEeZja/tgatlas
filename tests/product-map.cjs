const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const c={window:{},localStorage:{getItem:()=>null},console};vm.createContext(c);
for(const name of ['taxonomy','topics','classification-v11-taxonomy','classification-v11-runtime','layout-policy','layout','product-map'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',name+'.js'),'utf8'),c);
const view=c.window.AtlasView;
function row(username,subcategory,scope,country,regions=[]){return {id:username,username,name:username,description:'',subcategory,topic_group:c.window.Topics.byId[subcategory]?.group||'',geography_scope:scope,geography_country:country,geography_regions:regions};}
const rows=[row('local_news','v11_general_news','region','RU',['Москва']),row('ua_politics','v11_political_processes','country','UA'),row('regional_authority','v11_public_services','region','RU',['Карачаево-Черкесская Республика']),row('bank_channel','v11_banking_payments','country','RU'),row('military_unknown','v11_military_affairs','unknown',''),row('global_news','v11_general_news','global','UA'),row('personal_finance','v11_personal_finance','region','RU',['Москва'])];
const original=JSON.stringify(rows);
const geo=view.geographicView(rows).layout;
assert.deepEqual(Array.from(geo.nodes,n=>n.username).sort(),['local_news','regional_authority','ua_politics']);
assert(geo.nodes.every(view.geographicChannel));assert(geo.clusters.every(c=>!c.id.startsWith('context:')));
const topics=view.topicView(rows,null,new Map()).layout;
assert.equal(topics.nodes.length,rows.length);assert.equal(topics.geographyVisible,false);
assert(topics.clouds.every(c=>!c.label.includes('Москва')&&!c.label.includes('?')));
assert.equal(JSON.stringify(rows),original,'View changes must never mutate source metadata');
console.log('PASS: geographic mode includes relevant known coverage only; thematic fallback has no geographic subgroups and source data is immutable.');
