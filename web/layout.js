/* Seeded organic archipelagos: subject → conditional geographic neighbourhood.
 * Distances express curated category/coverage membership, not embedding similarity.
 */
(function () {
  "use strict";
  const policy = window.TG_MAP_POLICY || {regional_topics:[],conditional_topics:[],local_context_pattern:"a^",explicit_local_pattern:"a^",biography_pattern:"a^",international_pattern:"a^",proposal_region_weight:0.5};
  function geographyRole(ch) {
    const scope=ch.geography_scope;
    let regions=[...new Set(ch.geography_regions || (!scope && ch.region?[ch.region]:[]))].filter(Boolean).sort();
    const country=ch.geography_country || (!scope?ch.country:"") || "";
    if((country && country!=="RU") || ["unknown","nonlocal","global"].includes(scope))regions=[];
    const text=((ch.name||"")+" "+String(ch.description||"").split(/\r?\n/).filter((line)=>!new RegExp(policy.biography_pattern,"i").test(line)).join(" ")).toLowerCase().replace(/ё/g,"е");
    const source=ch.geography_source||"";
    const matched=(ch.geography_candidates||[]).filter(c=>c&&typeof c==="object"&&(regions.includes(c.region)||scope==="country"&&c.country===country&&!c.region));
    const proof=source==="manual" || ["title","title+description","profile_title","profile"].includes(source) || matched.some(c=>c.title_match||c.description_match);
    let relevant=policy.regional_topics.includes(ch.subcategory)||policy.conditional_topics.includes(ch.subcategory)&&proof&&new RegExp(policy.local_context_pattern,"i").test(text)||proof&&new RegExp(policy.explicit_local_pattern,"i").test(text);
    if(ch.classification_engine==="semantic-v11v2")relevant=relevant||ch.topic_granularity==="group"&&ch.topic_group==="v11_news_society"||["v11_medicine","v11_beauty","v11_psychology"].includes(ch.subcategory)&&(ch.geography_review_reasons||[]).includes("local_service_profile_location");
    if(new RegExp(policy.international_pattern,"i").test(text)&&source!=="manual")relevant=false;
    const provisional=Boolean(ch.geography_review_required);
    if(relevant&&regions.length)return {kind:"region",key:regions.join(" · "),label:regions.join(" · "),regions,country:country||"RU",weight:provisional?(proof?(policy.profile_region_weight??0.9):policy.proposal_region_weight):1,provisional};
    if(relevant&&scope==="country"&&country)return {kind:"country",key:"country:"+country,label:country,regions:[],country,weight:0,provisional};
    return {kind:"topic",key:"",label:"",regions:[],country,weight:0,provisional};
  }
  function nodeRadius(ch) {
    const n = Number(ch.subscribers) || 0;
    return n > 0 ? 4 + 3 * Math.min(1, Math.log10(n) / 7) : 4;
  }
  function rowBinding(ch) {
    const values=[String(ch.id||ch.username),ch.username,ch.name||"",ch.description||"",ch.subcategory||"other",ch.topic_group||"",ch.geography_scope||"",ch.geography_country||"",ch.geography_regions||[],ch.region||"",ch.country||"",ch.geography_source||"",Boolean(ch.geography_review_required),(ch.geography_candidates||[]).filter((c)=>c&&typeof c==="object").map((c)=>[c.region||"",Boolean(c.title_match),Boolean(c.description_match)])];
    if(ch.classification_engine==="semantic-v11v2")values.push(["semantic-v11v2",ch.topic_granularity||"",ch.geography_review_reasons||[],ch.classification_policy_sha256||"",ch.classification_implementation_sha256||""]);
    return JSON.stringify(values);
  }
  function fromArtifact(channels, artifact, mode="topic") {
    return inspectArtifact(channels,artifact,mode).layout;
  }
  function inspectArtifact(channels, artifact, mode="topic") {
    try { return {layout:readArtifact(channels,artifact,mode),code:"ok",reason:""}; }
    catch (error) { return {layout:null,code:error.code||"invalid_artifact",reason:error.code?error.message:"Повреждён формат семантического снимка"}; }
  }
  function reject(code,reason) {
    throw Object.assign(new Error(reason),{code});
  }
  function readArtifact(channels, artifact, mode="topic") {
    if(mode!=="topic")reject("region_mode","Выбрана группировка по регионам");
    if(!artifact)reject("missing_artifact","Снимок t-SNE не загружен");
    if(artifact.format!=="atlas-semantic-layout-v1"||!["umap","tsne"].includes(artifact.method))reject("invalid_format","Неизвестный формат семантического снимка");
    if(artifact.policy_sha256!==policy.fingerprint)reject("policy_mismatch","Версии правил карты и t-SNE различаются; обновите страницу");
    const age=Date.now()-Date.parse(artifact.created_at);
    if(!Number.isFinite(age))reject("invalid_date","Некорректная дата t-SNE");
    if(age<0)reject("future_artifact","Дата t-SNE находится в будущем; проверьте часы компьютера");
    if(age>7*86400000)reject("expired_artifact","Снимку t-SNE больше 7 дней");
    if(!artifact.layout||!Array.isArray(artifact.layout.nodes))reject("missing_nodes","В снимке t-SNE нет координат каналов");
    if(artifact.layout.nodes.length!==channels.length)reject("channel_count","Количество каналов отличается: t-SNE "+artifact.layout.nodes.length+", каталог "+channels.length);
    const source=new Map(channels.map((c)=>[String(c.id||c.username),c]));
    const bindings=artifact.bindings||{};
    const ids=new Set();
    const finite=(n)=>Number.isFinite(n)&&Math.abs(n)<1e8;
    for(const [collection,fields] of [[artifact.layout.clusters,["x","y","width","height","cx","cy","labelX","labelY"]],[artifact.layout.clouds,["x","y","radius","labelY"]]]) {
      if(!Array.isArray(collection))reject("invalid_groups","Повреждены группы t-SNE");
      const seen=new Set();
      for(const item of collection) {
        if(!item||typeof item.id!=="string"||seen.has(item.id)||fields.some((k)=>!finite(item[k])))reject("invalid_groups","Повреждена геометрия групп t-SNE");
        seen.add(item.id);
      }
    }
    const clusters=new Set((artifact.layout.clusters||[]).map((c)=>c.id));
    if(artifact.layout.annotations!==undefined) {
      if(!Array.isArray(artifact.layout.annotations))reject("invalid_annotations","Повреждены подписи t-SNE");
      const seen=new Set();
      for(const a of artifact.layout.annotations) {
        if(!a||typeof a.id!=="string"||seen.has(a.id)||typeof a.label!=="string"||!["category","topic","region","country"].includes(a.kind)||!finite(a.x)||!finite(a.y)||!Array.isArray(a.members)||a.members.some(id=>!source.has(id)))reject("invalid_annotations","Подписи t-SNE не соответствуют каталогу");
        seen.add(a.id);
      }
    }
    const nodes=[];
    for(const point of artifact.layout.nodes) {
      const ch=source.get(point.id);
      if(!ch||ids.has(point.id))reject("channel_identity","Состав каналов t-SNE отличается от каталога");
      const handle="@"+ch.username;
      if(ch.topic_source==="local")reject("local_correction","В этом браузере сохранена ручная тема "+handle+"; t-SNE рассчитан без неё");
      if(bindings[point.id] !== (ch.layout_source_binding||rowBinding(ch)))reject("binding_mismatch","Данные "+handle+" изменились после расчёта t-SNE");
      if(point.topic!==ch.subcategory||point.group!==(ch.topic_group||""))reject("topic_mismatch","Тема "+handle+" в браузере отличается от t-SNE; проверьте версии классификации");
      const focus=geographyRole(ch);
      if(!point.layout_geography||["kind","key","label","country","weight","provisional"].some(k=>point.layout_geography[k]!==focus[k])||JSON.stringify(point.layout_geography.regions)!==JSON.stringify(focus.regions))reject("geography_mismatch","География "+handle+" в браузере отличается от t-SNE");
      if(!Number.isFinite(point.x)||!Number.isFinite(point.y)||Math.abs(point.x)>1e8||Math.abs(point.y)>1e8||!clusters.has(point.cluster))reject("invalid_coordinates","Повреждены координаты "+handle);
      ids.add(point.id);
      if(point.x<0||point.y<0||point.x>artifact.layout.width||point.y>artifact.layout.height)reject("invalid_coordinates","Координаты "+handle+" выходят за границы карты");
      nodes.push({...ch,...point,id:ch.id||ch.username,radius:nodeRadius(ch),name:ch.name,username:ch.username});
    }
    if(!Number.isFinite(artifact.layout.width)||!Number.isFinite(artifact.layout.height)||artifact.layout.width<=0||artifact.layout.height<=0)reject("invalid_dimensions","Некорректный размер снимка t-SNE");
    return {...artifact.layout,nodes,method:artifact.method,evidence:artifact.evidence};
  }
  function hash(s) {
    let h = 2166136261;
    for (const c of String(s)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
    return h >>> 0;
  }
  function random(seed) {
    let a = hash(seed);
    return () => {
      a += 0x6d2b79f5;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function geo(ch) {
    // Explicit nonterritorial coverage must not inherit a legacy stored region.
    if (ch.geography_scope === "nonlocal")
      return "Без территориальной привязки";
    if (ch.geography_scope === "unknown") return "География не уточнена";
    if (ch.geography_regions?.length) return ch.geography_regions.join(" · ");
    if (ch.geography_scope === "country")
      return (
        "Вся страна · " + (ch.geography_country || ch.country || "не уточнена")
      );
    if (ch.geography_scope === "global") return "Международная повестка";
    if (ch.geography_scope) return "География не уточнена";
    return ch.region || "Без региона";
  }
  // Search rings scale with island size. The outer ring always has room;
  // unlike the old bounded spiral, no fallback can create a rightward row.
  function pack(items, gap) {
    const placed = [];
    for (const item of items) {
      let p = { x: 0, y: 0 };
      if (placed.length) {
        let found = false;
        const bound=Math.max(...placed.map((q)=>Math.hypot(q.x,q.y)+q.radius))+gap+item.radius;
        const step=Math.max(8,item.radius*.22);
        const phase=(hash(item.id || String(placed.length))/4294967296)*Math.PI*2;
        for (let ring=1; ring<=Math.ceil(bound/step)+1 && !found; ring++) {
          const d=ring*step;
          const samples=Math.min(256,Math.max(12,Math.ceil(2*Math.PI*d/(step*1.5))));
          for(let k=0;k<samples;k++) {
          const angle=phase+ring*2.399963229728653+k*Math.PI*2/samples;
          p = { x: Math.cos(angle) * d, y: Math.sin(angle) * d };
          if (
            placed.every(
              (q) =>
                Math.hypot(p.x - q.x, p.y - q.y) >=
                item.radius + q.radius + gap,
            )
          ) {
            found = true;
            break;
          }
          }
        }
        if (!found)
          p = {
            x:Math.cos(phase)*(bound+step),
            y:Math.sin(phase)*(bound+step),
          };
      }
      Object.assign(item, p);
      placed.push(item);
    }
    return placed;
  }
  function scatter(channels, seed) {
    const list = [...channels].sort(
      (a, b) =>
        hash(a.id) - hash(b.id) || String(a.id).localeCompare(String(b.id)),
    );
    const grid = new Map(),
      nodes = [];
    const cell = 24;
    let bound = Math.max(26, Math.sqrt(list.length) * 16);
    for (const ch of list) {
      const rnd = random(seed + ":" + ch.id);
      let x,
        y,
        r = nodeRadius(ch),
        placed = false;
      for (let k = 0; k < 10000; k++) {
        const angle = rnd() * Math.PI * 2,
          dist = Math.sqrt(rnd()) * bound;
        const warp =
          1 +
          0.13 * Math.sin(angle * 3 + (hash(seed) % 13)) +
          0.08 * Math.cos(angle * 5);
        x = Math.cos(angle) * dist * warp;
        y =
          Math.sin(angle) * dist * (0.82 + 0.12 * Math.sin(hash(seed))) * warp;
        const cx = Math.floor(x / cell),
          cy = Math.floor(y / cell);
        let good = true;
        for (let i = cx - 2; i <= cx + 2 && good; i++)
          for (let j = cy - 2; j <= cy + 2 && good; j++)
            for (const n of grid.get(i + ":" + j) || [])
              if (Math.hypot(x - n.x, y - n.y) < r + n.radius + 10) {
                good = false;
                break;
              }
        if (good) {
          const n = { ...ch, x, y, radius: r };
          nodes.push(n);
          const key = cx + ":" + cy;
          if (!grid.has(key)) grid.set(key, []);
          grid.get(key).push(n);
          placed = true;
          break;
        }
        if (k % 300 === 299) bound *= 1.04;
      }
      if (!placed) throw new Error("Не удалось разместить канал " + ch.id);
    }
    const radius =
      Math.max(30, ...nodes.map((n) => Math.hypot(n.x, n.y) + n.radius)) + 28;
    return { nodes, radius };
  }
  function hull(nodes) {
    const p = nodes
      .map((n) => ({ x: n.x, y: n.y }))
      .sort((a, b) => a.x - b.x || a.y - b.y);
    if (p.length < 3) return p;
    const cross = (o, a, b) =>
      (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    const low = [],
      high = [];
    for (const a of p) {
      while (low.length > 1 && cross(low.at(-2), low.at(-1), a) <= 0) low.pop();
      low.push(a);
    }
    for (const a of [...p].reverse()) {
      while (high.length > 1 && cross(high.at(-2), high.at(-1), a) <= 0)
        high.pop();
      high.push(a);
    }
    return low.slice(0, -1).concat(high.slice(0, -1));
  }
  function placeOnRussia(macros) {
    const centers = window.Topics.taxonomy.region_centers || {};
    const known = macros.filter((m) => centers[m.id]),
      extra = macros.filter((m) => !centers[m.id]);
    // LCC projection anchors; local collision relaxation preserves the continental silhouette.
    for (const m of known) {
      const [x, y] = centers[m.id];
      m.x = m.anchorX = x * 5000;
      m.y = m.anchorY = -y * 5000;
    }
    for (let pass = 0; pass < 260; pass++) {
      for (const m of known)
        if (pass < 100) {
          m.x += (m.anchorX - m.x) * 0.015;
          m.y += (m.anchorY - m.y) * 0.015;
        }
      for (let i = 0; i < known.length; i++)
        for (let j = i + 1; j < known.length; j++) {
          const a = known[i],
            b = known[j];
          let dx = b.x - a.x,
            dy = b.y - a.y,
            d = Math.hypot(dx, dy);
          const min = a.radius + b.radius + 35;
          if (d < min) {
            if (d < 0.001) {
              dx = 0.001;
              dy = 0.001;
              d = Math.hypot(dx, dy);
            }
            const f = ((min - d) / d) * 1.02;
            const massA = a.radius * a.radius,
              massB = b.radius * b.radius;
            const weightA = massB / (massA + massB),
              weightB = 1 - weightA;
            a.x -= dx * f * weightA;
            a.y -= dy * f * weightA;
            b.x += dx * f * weightB;
            b.y += dy * f * weightB;
          }
        }
    }
    let expand = 1;
    for (let i = 0; i < known.length; i++)
      for (let j = i + 1; j < known.length; j++)
        expand = Math.max(
          expand,
          (known[i].radius + known[j].radius + 35) /
            Math.max(
              0.001,
              Math.hypot(known[i].x - known[j].x, known[i].y - known[j].y),
            ),
        );
    for (const m of known) {
      m.x *= expand;
      m.y *= expand;
    }
    if (extra.length) {
      pack(extra, 50);
      const minY = Math.min(...extra.map((m) => m.y - m.radius));
      const y = known.length
        ? Math.max(...known.map((m) => m.y + m.radius)) + 180
        : 0;
      const cx = known.length
        ? known.reduce((n, m) => n + m.x, 0) / known.length
        : 0;
      for (const m of extra) {
        m.x += cx;
        m.y += y - minY;
      }
    }
  }
  function buildClusterLayout(channels, mode = "topic", options = {}) {
    const roleOf=options.roleOf || geographyRole;
    const groups = new Map();
    for (const ch of channels) {
      const t =
        window.Topics.groupsById?.[ch.topic_group] ||
        window.Topics.byId[ch.subcategory] ||
        window.Topics.byId.other;
      const id =
        mode === "region" ? (roleOf(ch).kind === "region" ? roleOf(ch).key : roleOf(ch).kind === "country" ? roleOf(ch).key : "context:"+t.id) : ch.topic_group ? "group:" + t.id : t.id;
      if (!groups.has(id))
        groups.set(id, {
          id,
          label: mode === "region" ? id.startsWith("context:") ? t.label+" · по теме" : id.startsWith("country:") ? (options.compactLabels?"":"Страна · ")+(window.TG_CLASSIFICATION_V11?.country_labels?.[id.slice(8)]||id.slice(8)) : id : t.label,
          color: mode === "region" ? "#a6cbb2" : t.color,
          channels: [],
        });
      groups.get(id).channels.push(ch);
    }
    const macros = [...groups.values()].sort(
      (a, b) =>
        b.channels.length - a.channels.length || a.id.localeCompare(b.id),
    );
    for (const macro of macros) {
      const buckets = new Map();
      for (const ch of macro.channels) {
        const focus=roleOf(ch);
        const key = ch.subcategory+(mode === "topic" && options.geography!==false && focus.key?"|"+focus.key:"");
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(ch);
      }
      macro.clouds = [...buckets]
        .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
        .map(([id, list]) => ({
          ...scatter(list, macro.id + ":" + id),
          id,
           label: (list[0].topic_granularity==="group"?"Подтема не уточнена":list[0].topic_granularity==="abstain"?"Тема не определена":window.Topics.byId[list[0].subcategory]?.label || list[0].subcategory) + (mode === "topic" && options.geography!==false && roleOf(list[0]).key?" · "+(roleOf(list[0]).kind==="country"?(window.TG_CLASSIFICATION_V11?.country_labels?.[roleOf(list[0]).country]||roleOf(list[0]).country):roleOf(list[0]).label)+(roleOf(list[0]).provisional?" ?":""):""),
        }));
      pack(macro.clouds, 24);
      macro.radius =
        Math.max(
          40,
          ...macro.clouds.map((c) => Math.hypot(c.x, c.y) + c.radius),
        ) + 45;
    }
    if (mode === "region") placeOnRussia(macros);
    else pack(macros, 90);
    const nodes = [],
      clusters = [],
      clouds = [];
    for (const m of macros) {
      const own = [];
      for (const c of m.clouds) {
        const ns = c.nodes.map((n) => ({
          ...n,
          x: n.x + c.x + m.x,
          y: n.y + c.y + m.y,
          cluster: m.id,
           layout_geography: roleOf(n),
          neighbourhood: m.id + ":" + c.id,
          color: window.Topics.byId[n.subcategory]?.color || "#83968c",
        }));
        nodes.push(...ns);
        own.push(...ns);
        clouds.push({
          id: m.id + ":" + c.id,
          cluster: m.id,
          label: c.label,
          color: m.color,
          count: ns.length,
          x: m.x + c.x,
          y: m.y + c.y,
          radius: c.radius,
          outline: hull(ns),
          labelY: Math.min(...ns.map((n) => n.y)) - 24,
        });
      }
      clusters.push({
        id: m.id,
        label: m.label,
        color: m.color,
        count: own.length,
        x: m.x - m.radius,
        y: m.y - m.radius,
        width: 2 * m.radius,
        height: 2 * m.radius,
        cx: m.x,
        cy: m.y,
        labelX: m.x+m.clouds[0].x,
        labelY:
          m.y +
          m.clouds[0].y +
          Math.min(...m.clouds[0].nodes.map((n) => n.y)) -
          65,
      });
    }
    if (!nodes.length)
      return { nodes, clusters, clouds, width: 640, height: 480, mode };
    const left = Math.min(...clusters.map((c) => c.x)) - 80,
      top = Math.min(...clusters.map((c) => c.y)) - 160;
    for (const n of nodes) {
      n.x -= left;
      n.y -= top;
    }
    for (const c of clusters) {
      c.x -= left;
      c.y -= top;
      c.cx -= left;
      c.cy -= top;
      c.labelX -= left;
      c.labelY -= top;
    }
    for (const c of clouds) {
      c.x -= left;
      c.y -= top;
      c.labelY -= top;
      for (const p of c.outline) {
        p.x -= left;
        p.y -= top;
      }
    }
    return {
      nodes,
      clusters,
      clouds,
      width: Math.max(640, ...clusters.map((c) => c.x + c.width + 80)),
      height: Math.max(480, ...clusters.map((c) => c.y + c.height + 100)),
      mode,
    };
  }
  window.Layout = { buildClusterLayout, nodeRadius, geographyGroup: geo, geographyRole, packCircles:pack, rowBinding, fromArtifact, inspectArtifact };
})();
