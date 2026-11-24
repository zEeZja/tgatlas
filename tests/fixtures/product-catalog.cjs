/* Synthetic data only: no owner exports, network, Python or ML required. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
module.exports=function productCatalog(size=1200){
 if(!Number.isInteger(size)||size<1200||size>6000)throw Error('Synthetic fixture size must be 1200–6000');
 const c={window:{},localStorage:{getItem:()=>null},console};vm.createContext(c);
 for(const name of ['taxonomy','topics','classification-v11-taxonomy','classification-v11-runtime','layout-policy','layout'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../../web',name+'.js'),'utf8'),c);
 const ids=['v11_general_news','v11_military_affairs','v11_banking_payments','v11_medicine'];
 const created=new Date().toISOString();
 const rows=Array.from({length:size},(_,i)=>{
  const topic=c.window.Topics.byId[ids[i%ids.length]],name=`Тестовый канал ${i}`,description='Синтетический профиль для проверки интерфейса.';
  return {id:`fixture_${i}`,username:`fixture_${i}`,name,description,subcategory:topic.id,topic_group:topic.group,
   classification_engine:'semantic-v11v2',classification_version:'11v2',classification_created_at:created,
   classification_profile:{name,description},classification_manual_dimensions:[],
   topic_granularity:'topic',topic_source:'semantic-v11v2-proposal',topic_review_required:true,
   geography_scope:'region',geography_country:'RU',geography_regions:[i%3?'Москва':'Санкт-Петербург'],
   geography_source:'profile',geography_review_required:true,geography_candidates:[],geography_review_reasons:[],
   post_routing:{usable:0,fetched_at:null},subscribers:100000+i};
 });
 const enriched=rows.map(row=>c.window.Topics.enrich(row));
 const layout=c.window.Layout.buildClusterLayout(enriched,'topic',{geography:false});
 const byId=new Map(rows.map(row=>[row.id,row]));
 for(const node of layout.nodes){const row=byId.get(node.id);node.topic=row.subcategory;node.group=row.topic_group;}
 layout.annotations=layout.clusters.map(cluster=>({id:'category:'+cluster.id,kind:'category',label:cluster.label,x:cluster.cx,y:cluster.cy,count:cluster.count,members:layout.nodes.filter(n=>n.cluster===cluster.id).map(n=>n.id)}));
 layout.annotations.push({id:'region:test',kind:'region',label:'Москва',x:0,y:0,count:1,members:[rows[0].id]});
 return {rows,artifact:{format:'atlas-semantic-layout-v1',method:'tsne',created_at:created,
  policy_sha256:c.window.TG_MAP_POLICY.fingerprint,bindings:Object.fromEntries(rows.map(row=>[row.id,c.window.Layout.rowBinding(row)])),
  evidence:{with_posts:0},layout}};
};
