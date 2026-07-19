// waku dashboard — formatters + chat card renderers + chatlog + streaming + send.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

const money = n => "$" + (n < 0.01 ? n.toFixed(4) : n.toFixed(2));
const secs = ms => ms==null ? "无" : (ms/1000).toFixed(1)+" 秒";
const gateDecision = v => ({skip:"跳过记忆", retrieve:"检索记忆"}[v] || v || "");
const evalStatus = v => ({pass:"通过", fail:"失败", skip:"跳过", skipped:"跳过",
  "not run":"未运行", open:"已开放", conditional:"有条件", closed:"已关闭"}[v] || v || "");
const releaseStatus = r => !r ? "" : (r.release ||
  (r.deterministic!=="pass" || r.judge==="fail" ? "closed" :
    r.judge==="pass" ? "open" : "conditional"));
const releaseClass = v => v==="open" ? "pass" : v==="closed" ? "fail" : "skip";
const sourceLabel = v => ({dashboard:"网页", telegram:"Telegram", voice:"语音", cli:"终端", brief:"晨间简报"}[v] || v || "");

const gateBadge = g => !g ? "" :
  `<span class="badge ${g.decision==="retrieve"?"retrieve":""}">检索门 · ${esc(gateDecision(g.decision))}</span><span class="meta" style="margin:0">${esc(g.reason||"")}</span>`;

// A tool call renders as a status row (dot + one-line summary); the raw output
// hides behind a disclosure so an ugly osascript error never floods the page.
const toolRow = x => `<div class="tool ${x.status||"ok"}">
  <div class="tool-head"><span class="dot ${x.status||"ok"}"></span><code>${esc(x.tool)}</code>
    ${x.summary?`<span style="color:var(--ink2)">${esc(x.summary)}</span>`:""}</div>
  ${x.output!==undefined?`<details><summary>参数与原始输出</summary>
    <pre>${esc(x.tool)}(${esc(JSON.stringify(x.args,null,1))})\n\n${esc(x.output)}</pre>
  </details>`:""}
</div>`;

// A stored history row -> a CHAT item. Assistant rows with saved telemetry
// (meta: gate/latency/iterations/tools) render as the FULL turn card, so a
// reopened thread looks just like when it was live. Rows without meta (from
// before this was saved, or another gateway) fall back to a plain card.
function histItem(m){
  if (m.role === "user") return {role:"user", text:m.content};
  if (m.meta) return {role:"waku", reply:m.content, gate:m.meta.gate,
                      tools:m.meta.tools, iterations:m.meta.iterations,
                      latency_ms:m.meta.latency_ms, model:m.meta.model};
  return {role:"waku", reply:m.content, historical:true};
}

const turnCard = t => `<div class="card">
  <div class="u">${esc(t.user_message)}</div>
  <div class="meta" style="margin-top:4px">${gateBadge(t.gate)}</div>
  ${(t.tools||[]).map(toolRow).join("")}
  <div class="r">${renderMarkdown(t.reply)}</div>
  <div class="meta">${esc((t.ts||"").replace("T"," ").slice(0,19))} · ${secs(t.latency_ms)} · ${t.iterations??"?"} 轮 · ${money(t.cost||0)}${t.consolidation?` · 归纳 ${t.consolidation.new_facts} 条事实`:""}</div>
</div>`;

const table = (heads, rows) => rows.length
  ? `<div class="card" style="padding:4px 8px"><table><tr>${heads.map(h=>`<th>${h}</th>`).join("")}</tr>${rows.join("")}</table></div>`
  : `<div class="card empty">这里还没有内容</div>`;

const gateSplit = s => {
  if (!(s.gate_skips + s.gate_retrieves))
    return `<div class="splitbar"><div class="seg-skip" style="width:100%;opacity:.35"></div></div>
      <div class="meta" style="margin-top:6px">还没有对话。发送消息后，检索门会开始判断是否需要读取记忆。</div>`;
  const tot = s.gate_skips + s.gate_retrieves;
  const skipPct = Math.round(s.gate_skips/tot*100), retPct = 100-skipPct;
  // only label a segment when it's wide enough to fit the text — otherwise a
  // 0%/tiny segment spills its label past the bar (the "0 retri" bug).
  const seg = (cls, n, label, pct) =>
    `<div class="${cls}" style="width:${pct}%">${pct>=14?`${n} ${label}`:""}</div>`;
  return `<div class="splitbar">
    ${seg("seg-skip", s.gate_skips, "次跳过", skipPct)}
    ${seg("seg-ret", s.gate_retrieves, "次检索", retPct)}
  </div><div class="meta" style="margin-top:6px">${skipPct}% 的对话无需读取记忆，减少了延迟和无关记忆的干扰。</div>`;
};

// --- Chat gateway: type here, watch the harness run (turns kept in memory)
const CHAT = [];
// The gate → tools → reply stage strip, shared by the live card and the
// completed/replayed card so the markup can't drift. `live` lights stages up
// (gate flips to done once decided, reply "on" once text streams); otherwise
// every stage is done and the strip carries the .tele class (hidden by the
// stats toggle). (.stages is flexbox, so inter-span whitespace is irrelevant.)
function stagesRow(t, live){
  const gateCls = live ? (t.gate ? "done" : "on") : "done";
  const replyCls = live ? (t.stream ? "on" : "") : "done";
  const tools = (t.tools||[]).map(x => `<span class="stage done">工具 · ${esc(x.tool)}</span>`).join("");
  return `<div class="stages${live?"":" tele"}">`
    + `<span class="stage ${gateCls}">检索门${t.gate?` · ${esc(gateDecision(t.gate.decision))}`:""}</span>`
    + tools + `<span class="stage ${replyCls}">回复</span></div>`;
}
// The per-turn telemetry footer: seconds · iterations · model · consolidation.
const teleFooter = t => `<div class="meta tele">${secs(t.latency_ms)} · ${t.iterations??"?"} 轮${
  t.model?` · ${esc(t.model)}`:""}${t.consolidation?` · 归纳 ${t.consolidation.new_facts} 条事实`:""}</div>`;

const chatTurnCard = t => `<div class="card">
  ${t.gate?`${stagesRow(t, false)}
    <div class="meta tele" style="margin:0 0 6px">${esc(t.gate.reason||"")}</div>`:""}
  ${(t.tools||[]).length?`<div class="tele">${(t.tools||[]).map(toolRow).join("")}</div>`:""}
  <div class="r" style="margin-top:8px">${renderMarkdown(t.reply)}</div>
  ${teleFooter(t)}
</div>`;

// While a turn runs we stream it live: stages light up as the harness reaches
// them, and the reply text appears token by token (with a blinking caret).
const streamingCard = m => `<div class="card">
  ${stagesRow(m, true)}
  ${m.gate&&m.gate.reason?`<div class="meta" style="margin:0 0 6px">${esc(m.gate.reason)}</div>`:""}
  ${(m.tools||[]).map(toolRow).join("")}
  ${m.stream
     ? `<div class="r" style="margin-top:8px">${renderMarkdown(m.stream)}<span class="caret"></span></div>`
     : `<div class="meta" style="margin:0">思考中&hellip;${m.started?` ${Math.round((Date.now()-m.started)/1000)} 秒`:""}${
         m.started && Date.now()-m.started > 20000
         ? `<br>仍在等待：较慢的模型，尤其是免费额度，可能需要排队。达到 WAKU_LLM_TIMEOUT 后会报错，不会无限等待。`
         : ""}</div>`}
</div>`;

// Messages loaded from history (a switched/opened conversation) have no live
// latency/iteration data, and their stored form carries an internal
// "[tools used: ...]" annotation — strip both so the thread reads cleanly.
const stripTools = t => (t || "").replace(/\s*\[tools used:[\s\S]*\]\s*$/, "").trim();
const historicalCard = m => `<div class="card"><div class="r">${renderMarkdown(stripTools(m.reply))}</div></div>`;

function renderChatLog(){
  if (!CHAT.length)
    return `<div class="empty" style="padding:6px 2px">可以在任何页面给 Waku 发消息。打开“总览”查看消息如何流经系统，或在“会话入口”查看所有渠道的对话。</div>`;
  return CHAT.map(m => m.role==="user"
      ? `<div class="bubble">${esc(m.text)}</div>`
      : m.pending ? streamingCard(m)
      : m.historical ? historicalCard(m)
      : chatTurnCard(m)).join("");
}

function syncChatLogs(){
  // one conversation, two surfaces: the Chat & watch tab and the side dock
  document.querySelectorAll(".chatlog").forEach(el => {
    el.innerHTML = renderChatLog();
    el.scrollTop = el.scrollHeight;      // dock scrolls its own container
  });
}

// One streamed harness event updates the live card in place.
function applyStreamEvent(pending, ev){
  if (ev.kind === "gate") pending.gate = {decision: ev.decision, reason: ev.reason};
  else if (ev.kind === "text") pending.stream = (pending.stream || "") + (ev.delta || "");
  else if (ev.kind === "tool"){
    (pending.tools = pending.tools || []).push({
      tool: ev.tool, args: ev.args, output: ev.output,
      status: (ev.output||"").toLowerCase().startsWith("error") ? "error" : "ok",
      summary: (ev.output || "").split(". ")[0].slice(0,120)});
    pending.stream = "";   // a new assistant turn begins after the tool result
  } else if (ev.kind === "done"){
    pending.pending = false; pending.stream = "";
    if (ev.error) pending.reply = "错误：" + ev.error;
    else Object.assign(pending, ev);   // reply, tools, gate, iterations, latency_ms, consolidation
  }
}

async function sendChat(fromInput){
  const input = fromInput || document.getElementById("msg") || document.getElementById("dmsg");
  const text = (input && input.value || "").trim();
  if (!text) return;
  input.value = "";
  CHAT.push({role:"user", text});
  const pending = {role:"waku", pending:true, stream:"", started: Date.now()};
  CHAT.push(pending);
  syncChatLogs();
  // tick the elapsed counter while we wait for the first token
  const ticker = setInterval(() => { if (pending.pending && !pending.stream) syncChatLogs(); }, 1000);
  try {
    const res = await fetch("/api/chat/stream", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:text})});
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0){
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        try { applyStreamEvent(pending, JSON.parse(line.slice(5).trim())); } catch(e){}
        syncChatLogs();
      }
    }
  } catch(e){ Object.assign(pending, {pending:false, reply:"错误："+e}); }
  clearInterval(ticker);
  if (pending.pending) pending.pending = false;   // stream ended without a 'done'
  syncChatLogs();
  input.focus();
}
function wireDock(){
  const b = document.getElementById("dsend"), i = document.getElementById("dmsg");
  if (b) b.onclick = () => sendChat(i);
  if (i) i.onkeydown = e => { if (e.key==="Enter") sendChat(i); };
  const close = document.getElementById("dock-close"), reopen = document.getElementById("dock-reopen");
  const setClosed = v => { document.body.classList.toggle("dock-closed", v); localStorage.setItem("dockClosed", v?"1":"0"); };
  if (close) close.onclick = () => setClosed(true);
  if (reopen) reopen.onclick = () => setClosed(false);
  const saved = localStorage.getItem("dockClosed");
  setClosed(saved === null ? window.innerWidth < 1180 : saved === "1");
  syncChatLogs();
}
