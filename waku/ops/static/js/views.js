// waku dashboard — subtab/db helpers, SQL console, Memory/Tools sub-views, VIEWS.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

// --- sub-tabs: keep long pages short by splitting them into hash-routed tabs
// (#memory/semantic, #database/facts). Each tab is a plain link, so it's
// bookmarkable and the architecture cards can deep-link straight to one.
function subtabBar(view, tabs, active){
  return `<div class="subtabs">${tabs.map(([key,label,n]) =>
    `<a class="subtab ${key===active?"on":""}" href="#${view}/${key}">${esc(label)}${
      n!=null?`<span class="n">${n}</span>`:""}</a>`).join("")}</div>`;
}

// A raw SQLite table, scrollable, with the column names AS the (indigo) sticky
// headers so the schema lines up over its data instead of floating above it.
function dbTable(t){
  if (!t.sample.length) return `<div class="card empty">暂无数据</div>`;
  const head = t.columns.map(c => `<th class="dbcol">${esc(c)}${
    t.types&&t.types[c]?`<small>${esc(t.types[c].toLowerCase())}</small>`:""}</th>`).join("");
  const body = t.sample.map(r => `<tr>${t.columns.map(c =>
    `<td class="dbcell">${esc(String(r[c]??"").slice(0,120))}</td>`).join("")}</tr>`).join("");
  return `<div class="scrolly"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="meta" style="margin-top:6px">显示 ${t.sample.length} / ${t.count} 行，最新数据在前</div>`;
}
const DB_DESC = {
  calendar_events: "create_event 工具写入的日历事件",
  facts: "语义记忆中的长期事实，对应“记忆 > 语义记忆”",
  episodes: "情景记忆中的带日期摘要，对应“记忆 > 情景记忆”",
  chat_log: "全部消息，按 session_id 标记，记忆归纳会读取这里",
};
const QUERY_EXAMPLES = [
  "SELECT role, content FROM chat_log ORDER BY id DESC LIMIT 10",
  "SELECT subject, content FROM facts",
  "SELECT session_id, COUNT(*) FROM chat_log GROUP BY session_id",
];
function dbQueryView(){
  return `<div class="meta" style="margin-bottom:10px">这是 <code>state.db</code> 的只读 SQL 控制台。
      仅允许执行 <code>SELECT</code>，数据库也以只读方式打开，因此这里不会修改任何数据。</div>
    <textarea class="sqlbox" id="sqlbox" spellcheck="false" onfocus="markEditing()" oninput="markEditing()">${esc(QUERY_EXAMPLES[0])}</textarea>
    <div style="margin:8px 0"><button class="save" onclick="runQuery()">运行</button>
      <span class="meta" style="margin-left:12px">示例：${QUERY_EXAMPLES.map(q=>`<span class="qexample" onclick="qFill(this.textContent)">${esc(q)}</span>`).join(" &nbsp; ")}</span></div>
    <div id="qout"></div>`;
}

// --- read-only SQL console (item: "a simple query editor like Supabase")
function qFill(sql){ const b=document.getElementById("sqlbox"); if(b){ b.value=sql; runQuery(); } }
async function runQuery(){
  editing = true;   // keep the 5s refresh from wiping the query + results
  const sql = (document.getElementById("sqlbox")||{}).value || "";
  const out = document.getElementById("qout");
  out.innerHTML = `<div class="meta">运行中…</div>`;
  const r = await postJSON("/api/query", {sql});
  if (r.error){ out.innerHTML = `<div class="card empty" style="color:var(--bad)">${esc(r.error)}</div>`; return; }
  if (!r.rows.length){ out.innerHTML = `<div class="card empty">查询结果为空</div>`; return; }
  out.innerHTML = `<div class="scrolly"><table><thead><tr>${
    r.columns.map(c=>`<th class="dbcol">${esc(c)}</th>`).join("")}</tr></thead><tbody>${
    r.rows.map(row=>`<tr>${row.map(v=>`<td class="dbcell">${esc(String(v).slice(0,120))}</td>`).join("")}</tr>`).join("")
    }</tbody></table></div><div class="meta" style="margin-top:6px">共 ${r.rows.length} 行</div>`;
}

// --- Memory sub-tabs. Memory is the friendly, per-pillar view of what persists;
// the Data tab shows the SAME rows as raw SQLite tables (see the explainer).
function memOverview(d){
  const s = d.stats;
  const pillars = [
    ["语义记忆","semantic",d.facts.length+" 条事实","关于你、相关人物和项目的长期事实"],
    ["情景记忆","episodic",d.episodes.length+" 条摘要","每次归纳生成一条带日期摘要，刻意保持精简"],
    ["程序性记忆","skills",d.skills.length+" 项技能","仅在相关时加载 SKILL.md，指导 Waku 如何行动"],
  ].map(([t,sub,n,desc]) => `<div class="box" style="min-width:0" onclick="location.hash='memory/${sub}'">
      <b>${t} <span class="meta" style="font-weight:400">· ${n}</span></b><span>${desc}</span></div>`).join("");
  return `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>记忆和数据库，是同一个文件的两种视图。</b>
      <div class="r">这里按记忆类型整理 Waku 记住的内容。<a class="reveal" onclick="location.hash='database'">数据库页面</a>
      则展示相同数据对应的原始 SQLite 表，以及 FTS5 关键词索引。底层都是
      <code>.waku/state.db</code>，只是查看角度不同。
      <br><br>有些助手使用单个 <code>MEMORY.md</code> 保存长期记忆。Waku 把可查询的数据放在
      <code>state.db</code> 中，包括事实、情景摘要和 FTS5 索引，同时每轮对话后生成易读的
      ${reveal("MEMORY.md","MEMORY.md")} 镜像。你既能直接打开文件，也能依靠数据库稳定查询。</div></div>
    <h2>三类记忆</h2>
    <div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">${pillars}</div>
    <h2>记忆检索门：这一轮真的需要读取记忆吗？</h2>${gateSplit(s)}
    <div class="meta" style="margin-top:8px">每次查询前，轻量模型会先判断本轮是否需要记忆。只有需要时才检索，避免无关记忆增加延迟或干扰回答。“运行与评测”页面也会展示相同的跳过和检索指标。</div>
    <div class="meta" style="margin-top:14px">相关文件：${reveal("state.db","state.db")} · ${reveal("MEMORY.md","MEMORY.md")} · ${reveal("SOUL.md","SOUL.md")} · ${reveal("skills","skills/")}</div>`;
}
function memSemantic(d){
  let h = `<div class="meta" style="margin-bottom:12px">从你告诉 Waku 的内容中提炼出的长期事实。这是最精简、复用最频繁的记忆层。你可以编辑或删除任意事实，修改会在下一轮对话生效。</div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>主题</th><th>事实</th><th>来源</th><th></th></tr>${
    d.facts.map(f => `<tr id="fact-${f.id}">
      <td><code>${esc(f.subject)}</code></td>
      <td class="fc">${esc(f.content)}</td>
      <td class="meta">${esc(f.source)}</td>
      <td style="white-space:nowrap"><a class="reveal" onclick="editFact(${f.id})">编辑</a> · <a class="reveal del" onclick="delMem('delete_fact',${f.id})">删除</a></td>
    </tr>`).join("")}</table></div>`;
  return h;
}
function memEpisodic(d){
  let h = `<div class="card" style="background:var(--accent-soft);border-color:var(--line2)">
    <b>为什么这里的内容不多？</b> <span class="r">每次归纳只会生成一条精炼的情景摘要，而不是保存每条原始消息。
    完整逐句对话保存在数据库页面的 <a class="reveal" onclick="location.hash='database/chat_log'"><code>chat_log</code> 表</a>中，
    情景记忆只保留其中值得回顾的重点。</span></div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>日期</th><th>情景摘要</th><th></th></tr>${
    d.episodes.map(e => `<tr><td class="meta">${esc(e.happened_at)}</td><td>${esc(e.summary)}</td>
      <td><a class="reveal del" onclick="delMem('delete_episode',${e.id})">删除</a></td></tr>`).join("")}</table></div>`;
  return h;
}
function memSkills(d){
  let h = `<div class="meta" style="margin-bottom:12px">程序性记忆是仅在消息匹配时加载的 Markdown 操作说明。你可以在聊天中教 Waku 新流程，让它调用 <code>create_skill</code>；也可以在下方直接编辑，或把 <code>SKILL.md</code> 放进${reveal("skills","技能目录")}。</div>`;
  h += d.skills.map((sk,i) => {
    const full = `---
name: ${sk.name}
description: ${sk.description}
---

${sk.body}`;
    return `<div class="card">
      <div class="u"><code>${esc(sk.name)}</code> <span class="meta" style="font-weight:400">· ${esc(sk.description)}</span>
        <span class="srcpill ${sk.editable?"":"apple"}" style="margin-left:6px">${sk.editable?"本地":"内置"}</span></div>
      <textarea class="editor" id="sk-${i}" style="min-height:150px;margin-top:8px" data-path="${esc(sk.path)}"
        oninput="dirty('sksave-${i}')" onfocus="markEditing()">${esc(full)}</textarea>
      <div style="margin-top:8px"><button class="save" id="sksave-${i}" disabled onclick="saveSkill(${i})">保存 SKILL.md</button>
        <span class="meta" id="skmsg-${i}" style="margin-left:10px">${esc(sk.rel)}</span></div></div>`;
  }).join("") || `<div class="card empty">尚未加载技能</div>`;
  return h;
}
function memSoul(d){
  return `<div class="meta" style="margin-bottom:12px"><code>SOUL.md</code> 定义 Waku 的人格和行为方式，每轮对话都会作为系统提示词加载。修改后，下一轮对话立即生效。</div>
    <div class="card"><textarea id="soul" class="editor" style="min-height:260px"
      oninput="dirty('soul-save')" onfocus="markEditing()">${esc(d.soul||"")}</textarea>
    <div style="margin-top:8px"><button class="save" id="soul-save" disabled onclick="saveSoul()">保存 SOUL.md</button>
      <span class="meta" id="soul-msg" style="margin-left:10px"></span></div></div>
    <div class="meta" style="margin-top:10px">${reveal("SOUL.md","在编辑器中打开 SOUL.md")}</div>`;
}
function memConsolidation(d){
  const distilled = d.facts.filter(f => f.source==="consolidation");
  let h = `<div class="card"><b>归纳流程。</b> <span class="r">每 ${d.consolidate_every} 轮对话，
    轻量模型会读取 ${"<code>chat_log</code>"} 中尚未处理的消息，提炼为长期<b>事实</b>（语义记忆）和一条<b>情景摘要</b>（情景记忆）。
    批量处理既能控制成本，也能给归纳模型足够上下文，判断哪些内容值得长期保存。</span></div>`;
  h += `<div class="tiles" style="margin-top:12px">
    <div class="tile"><b>${d.chat_pending}</b><span>待处理消息</span></div>
    <div class="tile"><b>${d.consolidate_every*2}</b><span>触发阈值</span></div>
    <div class="tile"><b>${distilled.length}</b><span>已归纳事实</span></div>
    <div class="tile"><b>${d.episodes.length}</b><span>情景摘要总数</span></div></div>`;
  h += `<h2>已归纳的事实</h2>`;
  h += table(["主题","事实","时间"], distilled.map(f =>
    `<tr><td><code>${esc(f.subject)}</code></td><td>${esc(f.content)}</td><td class="meta">${esc((f.created_at||"").slice(0,10))}</td></tr>`));
  h += `<div class="meta" style="margin-top:10px">这是一次记忆操作。每次归纳也会被<a class="reveal" onclick="location.hash='ops'">记录到追踪日志</a>，并可由模型裁判评测。</div>`;
  return h;
}

const TOOL_DESC_ZH = {
  create_event: "在本地日历中创建事件，用于安排、预约或规划具体时间的事项。",
  list_events: "读取本地日历，可列出全部未来事件或指定日期范围内的日程。",
  save_note: "把值得长期记住的人物、偏好或项目信息保存为事实。",
  send_message: "起草消息并放入本地发件箱，等待你检查后发送。",
  create_skill: "把你教给 Waku 的流程写成可复用的 SKILL.md，仅在你同意后创建。",
  manage_memory: "搜索、纠正或删除长期记忆中的事实与情景摘要。",
  update_soul: "把你的长期偏好和行为要求写入 Waku 的人格规则，下轮对话生效。",
  search_web: "搜索公开网页并返回标题、摘要和链接，可继续基于结果执行操作。",
  read_apple_calendar: "读取 Apple 日历中的真实日程，可按日期范围筛选。",
  read_apple_mail: "读取 Apple 邮件中的近期邮件，帮助整理摘要或晨间简报。",
  create_reminder: "在 Apple 提醒事项中创建待办或定时提醒。",
  create_note: "在 Apple 备忘录中创建笔记。",
  delegate_task: "把编程任务交给本地编码 Agent 执行，并返回运行结果。",
  run_command: "在沙箱中运行命令并读取输出，启用前还需要完善安全边界。",
  browse_web: "打开网页并读取或点击内容。只读检索目前可使用 search_web。",
  schedule_task: "让 Waku 安排周期性运行任务。当前可以用 make brief 配合系统 cron 实现。",
};
const TOOL_SOURCE_ZH = {
  flagship:"核心任务", web:"网页搜索", "self-management":"自我管理",
  apple:"Apple 生态", mcp:"MCP 服务", other:"其他",
};
const TOOL_BOX_ZH = {"Terminal tool":"终端工具", "Browser tool":"浏览器工具", "Cron Job":"定时任务"};

// Tools ▸ Results: the artifacts tool calls produced (kept distinct from the
// tools themselves — the old tab conflated capability with output).
function toolsResults(d){
  let h = `<div class="meta" style="margin-bottom:10px">这里展示工具调用实际写入的内容，也就是运行结果，而不是工具本身。</div>`;
  h += `<h2>日历事件 <span class="meta" style="font-weight:400">· 由 create_event 创建</span></h2>`;
  h += table(["事件","开始","结束","参与人"], d.calendar.map(e =>
    `<tr><td>${esc(e.title)}</td><td class="meta">${esc(e.start)}</td><td class="meta">${esc(e.end)}</td><td>${esc(e.attendees)}</td></tr>`));
  h += `<div class="meta" style="margin-bottom:16px">事件也会写入 <code>calendar.ics</code>。${reveal("calendar.ics","在访达中显示 calendar.ics")}，双击即可导入 Calendar.app。</div>`;
  h += `<h2>发件箱：待发送草稿 <span style="font-weight:400;text-transform:none;letter-spacing:0">· ${reveal("outbox","打开发件箱目录")}</span></h2>`;
  h += d.outbox.length ? d.outbox.map(o=>`<div class="card"><span class="u">${esc(o.name)}</span><div class="r">${esc(o.text)}</div></div>`).join("")
                       : `<div class="card empty">还没有消息草稿</div>`;
  return h;
}
// Tools ▸ MCP: external connectors. Shows live status + a copy-paste config so
// anyone can plug in their own server (scalable, not a one-off).
function toolsMCP(t){
  const m = t.mcp;
  let h = `<div class="card ${m.configured?"":""}" style="border-color:${m.live?"var(--good)":"var(--line2)"}">
    <b>模型上下文协议（MCP）${m.live?"，已连接":m.configured?"，已配置":"，尚未配置"}。</b>
    <div class="r">MCP 让 Waku 使用外部服务提供的工具，例如文件、GitHub 或数据库。
    工具名格式为 <code>&lt;server&gt;_&lt;tool&gt;</code>。${m.configured
      ? `已配置服务：${m.servers.map(s=>`<code>${esc(s)}</code>`).join(" ")}${m.live?"":"。发起一次对话即可连接。"}`
      : "目前没有配置 MCP 服务。"}</div></div>`;
  h += `<h2>30 秒接入一个 MCP 服务</h2><div class="card">
    <div class="meta">1. 安装可选依赖：<code>pip install -e '.[mcp]'</code></div>
    <div class="meta" style="margin-top:6px">2. 在${reveal("",".waku 目录")}中创建 <code>mcp.json</code>：</div>
    <pre style="font-family:var(--mono);font-size:11.5px;color:var(--ink2);white-space:pre-wrap;margin-top:8px">{"servers": [
  {"name": "fs", "command": "npx",
   "args": ["-y", "@modelcontextprotocol/server-filesystem", "${esc(D&&D.home||"")}"]}
]}</pre>
    <div class="meta" style="margin-top:8px">3. 重启 Dashboard。服务提供的工具会出现在
      <a class="reveal" onclick="location.hash='tools/available'">可用工具 > MCP 服务</a>中，并可在聊天中调用。</div></div>`;
  h += `<div class="meta" style="margin-top:12px">任何 MCP 服务都使用相同方式接入，无需修改 Waku 代码。技能也一样，只需把 <code>SKILL.md</code> 放进 ${reveal("skills","skills/")}。</div>`;
  return h;
}

const VIEWS = {
  // Gateway: ONE unified conversation across every channel (dashboard, telegram,
  // voice, cli) — the same loop + memory answer all of them. Each message is
  // tagged with where it came in, Hermes-style. You type in the dock on the right.
  // Gateway = an INBOX of conversations (like Slack/Intercom): one row per
  // conversation, tagged with its channel(s). Click one to open it in the chat
  // dock (the active thread). No longer a flat stream that duplicates the dock.
  gateway(d){
    const sessions = d.sessions || [];
    let h = `<div class="meta" style="margin-bottom:14px">网页、手机（Telegram）、语音和终端中的对话都由同一个 Waku 处理。点击任意会话即可在右侧聊天区继续交流。这里是会话列表，右侧显示当前打开的对话。</div>`;
    if (!sessions.length)
      return h + `<div class="card empty">还没有会话，可以先在右侧给 Waku 发一条消息。</div>`;
    h += sessions.map(s => {
      const tags = (s.sources||[]).map(src => `<span class="gwtag ${esc(src)}">${esc(sourceLabel(src))}</span>`).join("");
      const on = s.id === SESSION;
      return `<div class="toolcard" style="cursor:pointer${on?';border-color:var(--accent)':''}" onclick="openConversation('${esc(s.id)}')">
        <div class="tn" style="display:flex;justify-content:space-between;align-items:baseline;gap:10px">
          <span>${esc(s.title||s.id)} ${tags}</span>
          <span class="meta" style="font-weight:400;white-space:nowrap">${s.messages} 条消息 · ${esc((s.last_at||"").slice(0,16).replace("T"," "))}</span></div>
        <div class="td">${esc(s.last||"")}</div></div>`;
    }).join("");
    return h;
  },
  overview(d){
    const s = d.stats;
    const u = d.usage || {total_cost:0};
    const tiles = [
        [money(u.total_cost),"累计费用","money"],[secs(s.latency_avg),"平均每轮耗时",""],
        [s.turns,"对话轮数",""],[s.tool_calls,"工具调用",""],
        [d.facts.length,"长期事实",""],[d.calendar.length,"日历事件",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("");
    return `<div class="tiles">${tiles}</div>
    <h2>记忆检索门：先判断，再读取</h2>${gateSplit(s)}
    <h2 style="margin-top:26px">系统架构：点击任意模块查看详情 <span class="arch-status"></span></h2>
    ${archSVG(d)}
    <h2>最近一轮</h2>${d.turns.length?turnCard(d.turns[0]):'<div class="card empty">还没有对话，先给 Waku 发条消息吧。</div>'}`;
  },
  loop(d){
    return d.turns.length ? d.turns.map(turnCard).join("") : `<div class="card empty">还没有对话记录</div>`;
  },
  memory(d, sub){
    sub = sub || "overview";
    const tabs = [["overview","总览"],["semantic","语义记忆",d.facts.length],
      ["episodic","情景记忆",d.episodes.length],["skills","技能",d.skills.length],
      ["soul","人格"],["consolidation","记忆归纳",d.chat_pending]];
    let h = subtabBar("memory", tabs, sub);
    if (sub==="semantic") return h + memSemantic(d);
    if (sub==="episodic") return h + memEpisodic(d);
    if (sub==="skills") return h + memSkills(d);
    if (sub==="soul") return h + memSoul(d);
    if (sub==="consolidation") return h + memConsolidation(d);
    return h + memOverview(d);
  },
  settings(d){
    const st = d.settings || {providers:[]};
    let h = `<div class="card">当前 provider：<b>${esc(st.provider)}</b> · 主循环模型 <code>${esc(st.model)}</code> · 检索门与归纳模型 <code>${esc(st.small_model)}</code><div class="meta" style="margin:4px 0 0">两个任务由两个模型分工：主循环模型回答你的问题，轻量模型判断是否需要读取记忆，并把对话归纳为长期事实。</div></div>`;
    h += yourModelsCard(st);
    h += `<h2>模型服务与密钥（自带密钥，BYOK）</h2><div class="card">
      <label class="fld">模型服务（Provider）
        <select id="set-provider" onfocus="markEditing()">${st.providers.map(p=>`<option value="${p.name}" ${p.name===st.provider?"selected":""}>${p.name}${p.name===st.provider?`，当前模型：${esc(st.model)}`:`，默认模型：${esc(p.default_model)}`}</option>`).join("")}</select></label>
      ${st.base_url?`<div class="meta" style="margin:4px 0 8px">正在使用自定义接口：<code>${esc(st.base_url)}</code>（WAKU_BASE_URL${st.custom_key_set?" + WAKU_API_KEY":""}）。下方模型列表来自该接口。</div>`:""}
      <details class="adv"><summary>手动填写模型 ID（高级设置；也可以在下方目录中一键切换）</summary>
      <label class="fld">主循环模型（必须支持工具调用）<input id="set-model" list="model-list" onfocus="markEditing()" placeholder="留空则使用 provider 默认模型" value="${st.model===st.providers.find(p=>p.name===st.provider)?.default_model?"":esc(st.model)}"></label>
      <label class="fld">检索门与归纳模型（判断是否读取记忆并归纳长期事实，建议选择便宜、简洁的模型）<input id="set-small-model" list="model-list" onfocus="markEditing()" placeholder="留空则使用 provider 默认模型" value="${st.small_model===st.providers.find(p=>p.name===st.provider)?.default_small_model?"":esc(st.small_model)}"></label>
      <datalist id="model-list"></datalist>
      <div class="meta" id="model-list-msg" style="margin:4px 0 8px"></div></details>${(setTimeout(loadModelList,0),"")}
      <details class="adv" ${st.providers.find(p=>p.name===st.provider)?.key_set?"":"open"}><summary>API Key（${st.providers.find(p=>p.name===st.provider)?.key_set?`${esc(st.provider)} 已配置`:`${esc(st.provider)} 需要密钥`}）</summary>
      <div class="meta" style="margin:10px 0 4px">密钥只保存在本机 <code>.env</code> 中，页面仅显示是否已设置和末四位，不会回传完整密钥。输入框留空即可保留现有密钥。</div>
      ${st.providers.map(p=>`<label class="fld"><span>${p.name} 密钥 <span class="meta">(${p.key_env})</span>
        ${p.key_set?`<span class="srcpill" style="background:var(--good-soft);color:var(--good)">已设置 ····${esc(p.key_last4)}</span>`
                   :`<span class="srcpill apple">未设置</span>`}</span>
        <input type="password" data-key="${p.key_env}" placeholder="${p.key_set?"已保存密钥，留空可保持不变":"粘贴密钥"}"></label>`).join("")}
      </details>
      <div style="margin-top:12px"><button class="save" onclick="saveSettings()">保存并切换</button>
        <span class="meta" id="set-msg" style="margin-left:10px"></span></div>
    </div>
    <h2 id="catalog-h" style="display:none">模型目录：点击即可切换</h2>
    <div class="card" id="catalog" style="display:none"></div>
    <h2>网页搜索密钥（可选）</h2><div class="card">
      <div class="meta" style="margin-bottom:8px">免费的 <a class="reveal" onclick="window.open('https://tavily.com','_blank')">Tavily</a> 密钥可以提高 <code>search_web</code> 的稳定性。密钥同样保存在本机 <code>.env</code> 中。</div>
      <label class="fld"><span>Tavily 密钥 <span class="meta">(${esc(st.search_key_env||"TAVILY_API_KEY")})</span>
        ${st.search_key_set?`<span class="srcpill" style="background:var(--good-soft);color:var(--good)">已设置 ····${esc(st.search_key_last4)}</span>`
                          :`<span class="srcpill apple">未设置</span>`}</span>
        <input type="password" data-key="TAVILY_API_KEY" placeholder="${st.search_key_set?"已保存密钥，留空可保持不变":"粘贴密钥"}"></label>
      <div style="margin-top:12px"><button class="save" onclick="saveSettings()">保存</button>
        <span class="meta" style="margin-left:10px">立即生效，网页搜索无需重启</span></div>
      <div class="meta" style="margin-top:10px">提示：已经运行的终端、语音或 Telegram 入口需要重启后，才会切换 provider。</div>
    </div>`;
    return h;
  },
  tools(d, sub){
    const t = d.tools || {catalog:[], mcp:{configured:false,servers:[],live:false}, apple_on:false};
    sub = sub || "available";
    const tabs = [["available","可用工具",t.catalog.length],["results","运行结果"],
      ["mcp","MCP",t.mcp.servers.length||null]];
    let h = subtabBar("tools", tabs, sub);
    if (sub === "results") return h + toolsResults(d);
    if (sub === "mcp") return h + toolsMCP(t);
    // Available: what the agent CAN do (grouped by origin), not just what it did.
    h += `<div class="meta" style="margin-bottom:12px">这些是智能体在本轮对话中可以调用的能力。每个工具由模型可读的名称与说明、JSON 参数结构和一个 Python 函数组成。
      ${t.apple_on?"":"Apple 工具当前未开启，可设置 <code>WAKU_APPLE_TOOLS=1</code> 启用。"}还可以通过
      <a class="reveal" onclick="location.hash='tools/mcp'">MCP</a> 接入更多工具。</div>`;
    const SRC = [["flagship","核心任务：日程管理"],["web","网页搜索"],
      ["self-management","自我管理：维护自己的记忆"],
      ["apple","Apple 生态"],["mcp","MCP 服务"],["other","其他"]];
    SRC.forEach(([key,label]) => {
      const items = t.catalog.filter(c => c.source === key);
      if (!items.length) return;
      h += `<h2>${label}</h2>`;
      h += items.map(c => `<div class="toolcard">
        <div class="tn">${esc(c.name)}<span class="srcpill ${key==="mcp"?"mcp":key==="apple"?"apple":""}">${esc(TOOL_SOURCE_ZH[key]||key)}</span></div>
        <div class="td">${esc(TOOL_DESC_ZH[c.name]||c.description)}</div></div>`).join("");
    });
    // Roadmap: whiteboard boxes not wired in yet — set expectations, don't over-promise.
    if ((t.planned||[]).length){
      h += `<h2>即将推出 <span class="meta" style="font-weight:400">· 架构图中已有规划，目前尚未接入，可设置 <code>WAKU_EXPERIMENTAL=1</code> 体验</span></h2>`;
      h += t.planned.map(p => `<div class="toolcard" style="opacity:.7">
        <div class="tn">${esc(p.name)}<span class="srcpill apple">规划中 · ${esc(TOOL_BOX_ZH[p.box]||p.box)}</span></div>
        <div class="td">${esc(TOOL_DESC_ZH[p.name]||p.description)}</div></div>`).join("");
    }
    return h;
  },
  database(d, sub){
    // The persistence layer itself — one SQLite file, real tables, FTS5 index.
    // "Data" in the nav (plainer than "state.db"), but we keep saying state.db
    // because that's literally the filename you can open.
    const db = d.db || {tables:[], all_tables:[], fts:[], size:0, path:""};
    const tables = db.tables || [];
    sub = sub || "overview";
    const tabs = [["overview","总览"],
      ...tables.map(t => [t.name, t.name, t.count]),
      ["query","SQL 控制台"]];
    let h = subtabBar("database", tabs, sub);
    if (sub === "query") return h + dbQueryView();
    if (sub !== "overview"){
      const t = tables.find(x => x.name === sub);
      if (!t) return h + `<div class="card empty">找不到这张表</div>`;
      return h + `<div class="meta" style="margin-bottom:10px">${DB_DESC[t.name]||""}</div>` + dbTable(t);
    }
    const kb = (db.size/1024).toFixed(1);
    h += `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>数据库和记忆。</b> <span class="r">这里展示原始持久化层，也就是实际的 SQLite 表。
      <a class="reveal" onclick="location.hash='memory'">记忆页面</a>则以更易读的方式展示其中的事实、情景摘要、技能和人格。
      两者来自同一个文件。Waku 使用可查询的表保存记忆，同时也生成易读的 <code>MEMORY.md</code> 镜像。</span></div>`;
    h += `<div class="card">
      <div class="u" style="font-family:var(--mono);font-size:12.5px;word-break:break-all">${esc(db.path)}</div>
      <div class="meta">磁盘占用 ${kb} KB · SQLite + FTS5 · 可直接运行：<code>sqlite3 .waku/state.db</code></div>
      <div class="meta" style="margin-top:8px">${reveal("state.db","在访达中显示 state.db")} &nbsp;·&nbsp; ${reveal("","打开 .waku 目录")}</div></div>`;
    h += `<h2>数据表：点击上方标签或下方任意一行</h2>`;
    h += table(["表名","行数","保存内容"], tables.map(t =>
      `<tr><td><a class="reveal" onclick="location.hash='database/${esc(t.name)}'"><code>${esc(t.name)}</code></a></td>
        <td class="meta">${t.count}</td><td class="meta">${DB_DESC[t.name]||""}</td></tr>`));
    h += `<h2>FTS5 关键词索引</h2><div class="card"><code>*_fts</code> 虚拟表及对应的
      <code>*_fts_data</code>/<code>*_fts_idx</code> 影子表让记忆可以按关键词搜索，无需向量嵌入或向量数据库。
      检索门查询的“关键词 top-k”就来自这里。
      <div class="meta" style="margin-top:8px">全部 ${db.all_tables.length} 张表：${db.all_tables.map(t=>`<code>${esc(t)}</code>`).join(" ")}</div></div>`;
    return h;
  },
  ops(d){
    const s = d.stats;
    const u = d.usage || {calls:0,total_in:0,total_out:0,total_cost:0,by_day:[],by_provider:[]};
    const release = releaseStatus(d.eval_report);
    let h = `<div class="tiles">${[
        [money(u.total_cost),"累计费用","money"],[u.total_in.toLocaleString(),"累计输入 token",""],
        [u.total_out.toLocaleString(),"累计输出 token",""],[u.calls.toLocaleString(),"LLM 调用",""],
        [secs(s.latency_avg),"平均每轮耗时",""],[`${s.tool_errors}`,"工具错误",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("")}</div>`;

    h += `<h2>费用 <span class="meta" style="font-weight:400">· 永久账本，演示数据重置后仍会保留</span></h2>`;
    h += `<div class="card"><span class="r">每次 LLM 调用的 token 用量都会追加记录到
      <code>.waku/usage.jsonl</code>，不会自动清空。美元费用根据 token 数和当前价格估算，token 数是原始记录。${reveal("usage.jsonl","打开 usage.jsonl")}</span></div>`;
    if ((u.by_provider||[]).length){
      h += table(["provider","LLM 调用","输入 token","输出 token","预估费用"], u.by_provider.map(p =>
        `<tr><td><code>${esc(p.provider)}</code></td><td class="meta">${p.calls}</td>
          <td class="meta">${p.in.toLocaleString()}</td><td class="meta">${p.out.toLocaleString()}</td>
          <td class="meta">${money(p.cost)}</td></tr>`));
    }
    if ((u.by_day||[]).length){
      h += `<h2>每日费用</h2>`;
      h += table(["日期","LLM 调用","输入 token","输出 token","预估费用"], u.by_day.map(r =>
        `<tr><td class="meta">${esc(r.date)}</td><td class="meta">${r.calls}</td>
          <td class="meta">${r.in.toLocaleString()}</td><td class="meta">${r.out.toLocaleString()}</td>
          <td class="meta">${money(r.cost)}</td></tr>`));
    }

    h += `<h2>记忆检索门：哪些对话读取了记忆</h2>${gateSplit(s)}`;
    const decided = d.turns.filter(t => t.gate);
    if (decided.length){
      h += `<div class="meta" style="margin:8px 0">以下是每轮实际判断，最新记录在前：</div>`;
      h += table(["对话","判断","原因"], decided.slice(0,10).map(t =>
        `<tr><td>${esc((t.user_message||"").slice(0,44))}</td>
          <td><span class="pill ${t.gate.decision==="skip"?"skip":"pass"}">${esc(gateDecision(t.gate.decision))}</span></td>
          <td class="meta">${esc(t.gate.reason||"")}</td></tr>`));
    }

    h += `<h2>发布门 <span class="meta" style="font-weight:400">· 判断当前版本能否发布</span></h2>`;
    h += `<div class="card"><span class="r">发布提示词、模型或检索策略改动前，运行 <code>make gate</code>：确定性测试必须 100% 通过；有可用密钥时，模型裁判也必须达到阈值。未运行模型裁判时只会得到“有条件”结果，不代表完整语义门禁通过。每次手动运行都会留下记录。</span></div>`;
    h += d.eval_report ? `<div class="card">
        <span class="pill ${releaseClass(release)}">发布门 · ${evalStatus(release)}</span>
        <span class="pill ${d.eval_report.deterministic}">确定性测试 · ${evalStatus(d.eval_report.deterministic)}</span>
        <span class="pill ${d.eval_report.judge==="pass"?"pass":d.eval_report.judge==="fail"?"fail":"skip"}" style="margin-left:8px">模型裁判 · ${evalStatus(d.eval_report.judge)}</span>
        <div class="meta">上次运行：${esc(d.eval_report.ran_at)} · 使用 <code>make gate</code> 重新评测</div></div>`
      : `<div class="card empty">尚未运行。执行 <code>make gate</code> 后，这里会显示结果。</div>`;

    if ((d.eval_history||[]).length){
      const cnt = s => s ? `${s.passed||0} 通过 · ${s.failed||0} 失败` : "无";
      h += `<h2>评测历史</h2>`;
      h += table(["时间","发布门","确定性测试","模型裁判","数量"], d.eval_history.map(r =>
        `<tr><td class="meta">${esc((r.ran_at||"").replace("T"," ").slice(0,19))}</td>
         <td><span class="pill ${releaseClass(releaseStatus(r))}">${esc(evalStatus(releaseStatus(r)))}</span></td>
         <td><span class="pill ${r.deterministic}">${esc(evalStatus(r.deterministic))}</span></td>
         <td><span class="pill ${r.judge==="pass"?"pass":r.judge==="fail"?"fail":"skip"}">${esc(evalStatus(r.judge))}</span></td>
         <td class="meta">确定性 ${cnt(r.suites&&r.suites.deterministic)} · 裁判 ${cnt(r.suites&&r.suites.judge)}</td></tr>`));
    }

    h += `<h2>最慢的对话</h2>`;
    const slow = [...d.turns].filter(t=>t.latency_ms!=null).sort((a,b)=>b.latency_ms-a.latency_ms).slice(0,6);
    h += table(["对话","耗时","费用","工具"], slow.map(t =>
      `<tr><td>${esc((t.user_message||"").slice(0,48))}</td><td class="meta">${secs(t.latency_ms)}</td><td class="meta">${money(t.cost||0)}</td><td class="meta">${(t.tools||[]).map(x=>x.tool).join(", ")||"无"}</td></tr>`));

    h += `<h2>追踪日志 <span class="meta" style="font-weight:400">· 每轮对话都记录为 JSONL，始终开启</span></h2>`;
    h += `<div class="card"><span class="r"><code>traces/</code> 中共有 ${s.trace_files} 个追踪文件${
      d.trace_file?`，最新文件：<code>${esc(d.trace_file)}</code>`:""}。${reveal("traces","打开追踪目录")}。
      追踪日志按顺序记录“发生了什么”，以下是最近几行：</span></div>`;
    h += (d.trace_tail||[]).length ? table(["事件","详情","时间"], d.trace_tail.map(e =>
        `<tr><td><code>${esc(e.type)}</code></td><td class="meta">${esc(String(e.detail).slice(0,60))}</td>
          <td class="meta">${esc((e.ts||"").replace("T"," ").slice(0,19))}</td></tr>`))
      : `<div class="card empty">还没有追踪记录，先和 Waku 对话吧。</div>`;
    h += `<div class="meta" style="margin-top:8px">查看 Span 瀑布图：运行 <code>make trace</code>，并设置 <code>OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317</code>。</div>`;

    if (d.wake_scans.length){
      h += `<h2>语音：接近唤醒词的识别结果</h2>`;
      h += table(["识别内容","时间"], d.wake_scans.map(w =>
        `<tr><td>${esc(w.heard)}</td><td class="meta">${esc((w.ts||"").replace("T"," ").slice(0,19))}</td></tr>`));
    }
    return h;
  },
};
