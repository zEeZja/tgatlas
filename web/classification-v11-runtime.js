/* Primary cached V11. No model inference or network requests in the browser. */
(() => {
  "use strict";
  const runtime = window.TG_CLASSIFICATION_V11;
  if (!runtime?.enabled || !window.Topics) return;
  const api = window.Topics;
  for (const topic of runtime.topics) api.byId[topic.id] = topic;
  for (const group of runtime.groups) api.groupsById[group.id] = group;
  api.taxonomy.topics.unshift(...runtime.topics);
  api.taxonomy.groups.unshift(...runtime.groups);
  api.taxonomy.sections.push({id:"v11",label:"Основная сетка V11"});
  api.taxonomy.priority_groups = [...runtime.groups.map((g)=>g.id), ...(api.taxonomy.priority_groups||[])];
  const legacyEnrich = api.enrich;
  const topicKeys = ["subcategory","primary_category","topic_group","topic_group_label","topic_category","topic_path","topic_granularity","topic_source","topic_score","topic_score_kind","topic_margin","topic_candidates","topic_tags","secondary_topics","broader_topics","topic_evidence","topic_review_required","topic_assignment_status"];
  function description(value) {
    return String(value||"").split(/\r?\n/).map((line)=>line.split(/(?:по (?:вопросам )?(?:реклам|сотрудничеств)|разместить реклам|реклам(?:а|ный отдел)\s*[:👉📩]|для связи\s*[:👉]|обратная связь\s*[:👉]|регистраци[яи] в|зарегистрирован|заявление (?:на|ркн)|реестр ркн|(?<![\p{L}\p{N}_])ркн\s*[:№]|\bpr\s*:)/iu)[0].replace(/https?:\/\/\S+|\b(?:t\.me|vk\.com|www\.)\/??\S*|[\w.+-]+@[\w.-]+\.\w+|@[\w]+/giu," ").trim()).filter(Boolean).join("\n");
  }
  api.enrich = (channel, options = {}) => {
    const base = legacyEnrich(channel, options);
    const v2=channel.classification_engine==="semantic-v11v2";
    if (!["semantic-v11","semantic-v11v2"].includes(channel.classification_engine) || channel.classification_taxonomy_sha256 && channel.classification_taxonomy_sha256!==runtime.fingerprint) return base;
    const snapshot = channel.classification_profile;
    const at = channel.post_routing?.fetched_at;
    const age = at ? Date.now()-Date.parse(at) : 0;
    const createdAge=v2?Date.now()-Date.parse(channel.classification_created_at):0;
    if (!snapshot || snapshot.name!==(channel.name||"") || snapshot.description!==description(channel.description) ||
        !Number.isFinite(age) || age<0 || age>168*3600000 || !api.byId[channel.subcategory] ||
        !Number.isFinite(createdAge)||createdAge<0||createdAge>168*3600000||
        v2&&(!api.groupsById[channel.topic_group]||!['topic','group','abstain'].includes(channel.topic_granularity)||
          channel.topic_granularity!=="topic"&&channel.subcategory!=="other")) return base;
    const result = {...base, classification_engine:channel.classification_engine, classification_version:v2?"11v2":11,
      classification_created_at:channel.classification_created_at,
      classification_manual_dimensions:channel.classification_manual_dimensions,
      classification_model:channel.classification_model, classification_review_reasons:channel.classification_review_reasons,
      classification_profile:snapshot, post_routing:channel.post_routing, legacy_classification:channel.legacy_classification};
    const explicit = channel.classification_manual_dimensions || [];
    if (base.topic_source!=="local" && (base.topic_source!=="manual" || explicit.includes("topic"))) {
      for (const key of topicKeys) if(Object.hasOwn(channel,key))result[key]=channel[key];
      if(v2)for(const key of ['topic_suggested','topic_group_candidates','classification_policy_sha256','classification_implementation_sha256','classification_geography_policy_sha256'])if(Object.hasOwn(channel,key))result[key]=channel[key];
    } else if (base.topic_source==="manual") {
      const mapped = runtime.legacy_migration[base.subcategory];
      if (mapped) {
        const topic = api.byId[mapped];
        Object.assign(result,{subcategory:mapped,primary_category:mapped,topic_group:topic.group,topic_group_label:api.groupsById[topic.group].label,topic_category:"v11",topic_granularity:"topic",topic_path:[topic.group,mapped],topic_source:"manual",topic_review_required:false,topic_assignment_status:"manual_migrated",topic_score:null,topic_tags:[mapped],secondary_topics:[]});
      }
    }
    if(base.geography_source!=="manual" || explicit.includes("geography")) {
      for(const [key,value] of Object.entries(channel))if(key.startsWith("geography_"))result[key]=value;
    }
    if(result.geography_country && result.geography_country!=="RU" && ["region","multi_region"].includes(result.geography_scope)) {
      result.geography_scope="country";result.geography_regions=[];
    }
    return result;
  };
})();
