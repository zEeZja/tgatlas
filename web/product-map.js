/* Presentation-only views. Source geography, manual labels and published coordinates stay intact. */
(() => {
  'use strict';
  const geoTopics=new Set(['general','emergencies','politics','world','military','government','media','v11_general_news','v11_urban_life','v11_incidents','v11_political_processes','v11_international_relations','v11_military_affairs','v11_public_services']);
  function coverageRole(ch) {
    const scope=ch.geography_scope,country=ch.geography_country||'';
    const regions=country==='RU'&&['region','multi_region'].includes(scope)?[...new Set(ch.geography_regions||[])].filter(Boolean).sort():[];
    if(regions.length)return {kind:'region',key:regions.join(' · '),label:regions.join(' · '),country,regions,weight:1,provisional:Boolean(ch.geography_review_required)};
    if(country&&['country','region','multi_region'].includes(scope))return {kind:'country',key:'country:'+country,label:country,country,regions:[],weight:0,provisional:Boolean(ch.geography_review_required)};
    return {kind:'topic',key:'',label:'',country:'',regions:[],weight:0,provisional:false};
  }
  function geographicChannel(ch) {
    const subject=geoTopics.has(ch.subcategory)||ch.topic_granularity==='group'&&['v11_news_society','v11_politics_world'].includes(ch.topic_group);
    return subject&&coverageRole(ch).kind!=='topic';
  }
  function topicView(channels, artifact, originals) {
    let diagnosis=window.Layout.inspectArtifact(channels,artifact);
    let overlay=false;
    if(!diagnosis.layout&&channels.some(ch=>ch.topic_source==='local')) {
      const baseline=channels.map(ch=>{
        if(ch.topic_source!=='local')return ch;
        const raw=originals.get(ch.id);
        return raw?{...window.Topics.enrich(raw,{ignoreLocal:true}),layout_source_binding:window.Layout.rowBinding(raw)}:ch;
      });
      const checked=window.Layout.inspectArtifact(baseline,artifact);
      if(checked.layout){diagnosis=checked;overlay=true;}
    }
    let result=diagnosis.layout;
    if(result) {
      const byId=new Map(channels.map(ch=>[ch.id,ch]));
      result={...result,nodes:result.nodes.map(n=>{
        const ch=byId.get(n.id);
        return {...n,...ch,x:n.x,y:n.y,radius:n.radius,cluster:n.cluster,neighbourhood:n.neighbourhood,
          topic:ch.subcategory,group:ch.topic_group||'',color:window.Topics.groupsById[ch.topic_group]?.color||n.color};
      })};
      if(overlay)diagnosis={...diagnosis,code:'local_overlay',reason:'Координаты t-SNE сохранены; ручные темы применены без пересчёта проекции'};
    } else result=window.Layout.buildClusterLayout(channels,'topic',{geography:false});
    // Captions only: existing semantic coordinates may include the historical
    // geographic conditioning. This view does not claim a new content-only ML run.
    result={...result,geographyVisible:false,annotations:result.annotations?.filter(a=>!['region','country'].includes(a.kind))};
    return {layout:result,code:diagnosis.code,reason:diagnosis.reason};
  }
  function geographicView(channels) {
    const rows=channels.filter(geographicChannel);
    const layout=window.Layout.buildClusterLayout(rows,'region',{roleOf:coverageRole,compactLabels:true});
    return {layout:{...layout,geographyVisible:true},code:'geography_view',reason:'Тематический отбор каналов с указанной территорией; не физическое положение авторов'};
  }
  window.AtlasView={coverageRole,geographicChannel,topicView,geographicView};
})();
