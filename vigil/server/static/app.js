/* Vigil station: a brief that writes itself. One WebSocket in, components routed into a fixed reading order. */
(function () {
  const { render, esc } = window.VigilComponents;
  const $ = (id) => document.getElementById(id);

  const SLOT = {
    IncidentHeader: "slot-header", SeverityBanner: "slot-banner", FacilityMap: "slot-map",
    SensorPanel: "slot-readings", TrendTimeline: "slot-readings", AnomalyAssessment: "slot-sit",
    PeopleAtRisk: "slot-sit", EvacuationRoute: "slot-sit",
    ResponseOptions: "slot-todo", Runbook: "slot-todo", ExternalContext: "slot-context",
    DecisionPanel: null, DispatchComposer: null, MonitorPanel: "slot-todo", Sources: null,
  };
  const SECTION_OF = { "slot-context": "sec-context" };

  const state = { team: "", dryRun: true, following: true, runId: null, facility: null, components: new Map(), nodes: new Map(), sources: [], routes: [], selected: [], decision: null };

  function toast(text, cls) { const t = document.createElement("div"); t.className = "toast " + (cls || ""); t.textContent = text; $("toasts").appendChild(t); setTimeout(() => t.remove(), 6000); }
  function log(text, level, t) { const d = document.createElement("div"); d.className = level || ""; d.innerHTML = `<span class="t">${t != null ? Number(t).toFixed(1) + "s" : ""}</span>${esc(text)}`; const tl = $("timeline"); tl.appendChild(d); tl.scrollTop = tl.scrollHeight; }
  async function api(path, body) { const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}); if (!res.ok) throw new Error((await res.text()) || res.statusText); return res.json(); }
  const show = (id, on = true) => $(id).classList.toggle("hidden", !on);
  function setStatus(text, cls) { const p = $("pill-status"); p.textContent = text; p.className = cls || ""; }
  function step(name) { const order = ["classify", "investigate", "compile", "decide", "act"]; const j = order.indexOf(name); document.querySelectorAll("#stepper span").forEach((s) => { const i = order.indexOf(s.dataset.step); s.className = i < j ? "done" : i === j ? "active" : ""; }); }

  const ctx = {
    get facility() { return state.facility; }, get routes() { return state.routes; }, get selected() { return state.selected; },
    select(ids) { state.selected = ids; if (state.runId) api(`/api/runs/${state.runId}/select`, { option_ids: ids }).catch(() => {}); },
  };

  function reset() {
    state.components.clear(); state.nodes.clear(); state.sources = []; state.routes = []; state.selected = []; state.decision = null;
    ["slot-header", "slot-banner", "slot-map", "slot-readings", "slot-sit", "slot-todo", "slot-context", "sources", "action-results", "timeline", "dec-alts", "metrics"].forEach((id) => { $(id).innerHTML = ""; });
    ["sec-context", "sec-sources"].forEach((id) => show(id, false));
    show("welcome", false); show("fold", true); show("below", true); show("decision", false);
    document.querySelectorAll(".lane").forEach((l) => { l.className = "lane"; l.querySelector(".state").textContent = "idle"; l.querySelector(".lane-b").innerHTML = ""; });
    document.querySelectorAll("#stepper span").forEach((s) => (s.className = ""));
    $("btn-act").disabled = false;
  }

  function mount(comp) {
    state.components.set(comp.key, comp);
    if (comp.component === "Sources") { renderSources(comp.props.sources || []); return; }
    if (comp.component === "DecisionPanel") { showDecisionPanel(comp.props); return; }
    if (comp.component === "DispatchComposer") { $("dispatch-text").value = comp.props.message || ""; return; }
    if (comp.component === "EvacuationRoute") { state.routes = comp.props.routes || []; const fm = [...state.components.values()].find((c) => c.component === "FacilityMap"); if (fm) mount(fm); }
    const slotId = SLOT[comp.component] === undefined ? "slot-context" : SLOT[comp.component];
    if (!slotId) return;
    const node = render(comp, ctx);
    if (!node) return;
    node.dataset.key = comp.key;
    node.style.order = comp.order || 0;
    if (comp.provisional) node.classList.add("provisional");
    const existing = state.nodes.get(comp.key);
    if (existing) { existing.replaceWith(node); node.classList.add("updated"); } else $(slotId).appendChild(node);
    state.nodes.set(comp.key, node);
    if (SECTION_OF[slotId]) show(SECTION_OF[slotId], true);
    node.querySelectorAll("[data-cite]").forEach((c) => c.addEventListener("click", () => highlightSource(c.dataset.cite)));
  }
  function unmount(key) { const n = state.nodes.get(key); if (n) { n.remove(); state.nodes.delete(key); } state.components.delete(key); }

  function renderSources(sources) {
    state.sources = sources; show("sec-sources", sources.length > 0);
    $("sources").innerHTML = sources.map((s) => `<div class="src" id="src-${esc(s.id)}"><span class="id">${esc(s.id)}</span><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a><span class="kind">${esc(s.kind)}</span><div class="sn">${esc(s.snippet)}</div></div>`).join("");
  }
  function highlightSource(id) { document.querySelectorAll(".src.hl").forEach((s) => s.classList.remove("hl")); const n = $("src-" + id); if (n) { n.classList.add("hl"); n.scrollIntoView({ block: "center", behavior: "smooth" }); } }

  function lane(agent, stateName, html) {
    const l = document.querySelector(`.lane[data-agent="${agent}"]`); if (!l) return;
    l.className = "lane " + stateName; l.querySelector(".state").textContent = stateName;
    if (html) { const b = l.querySelector(".lane-b"); const d = document.createElement("span"); d.className = "evt"; d.innerHTML = html; b.appendChild(d); b.scrollLeft = b.scrollWidth; }
  }

  function showDecisionPanel(p) {
    show("decision", true);
    const chip = $("dec-action"); chip.textContent = p.action; chip.className = "action-chip " + p.action;
    $("dec-conf").textContent = `confidence ${Math.round(p.confidence * 100)}%`;
    $("dec-why").textContent = p.rationale;
    $("dec-alts").innerHTML = (p.alternatives || []).map((a) => `<div><b>${esc(a.action)}</b> ${esc(a.why_not)}</div>`).join("");
  }
  function showDecision(d) {
    state.decision = d; showDecisionPanel(d);
    $("dispatch-text").value = d.dispatch_message || ""; $("override-action").value = ""; $("btn-act").disabled = false;
    lane("commander", "done", `decided <b>${esc(d.action)}</b> · ${Math.round(d.confidence * 100)}%`);
    if (d.guardrail_note) { log("guardrail: " + d.guardrail_note, "warn"); lane("commander", "done", `policy: ${esc(d.guardrail_note)}`); }
    step("act");
  }
  function showMetrics(m) {
    const t = m.timings || {};
    $("metrics").innerHTML = [["first tile", t.first_component_ms], ["decided", t.decided_ms]].filter(([, v]) => v != null).map(([k, v]) => `<i>${k} ${(v / 1000).toFixed(1)}s</i>`).join("") + (m.llm_calls != null ? `<i>${m.llm_calls} model calls</i><i>${m.sources} sources</i>` : "");
  }
  function addActionResult(r) {
    const d = r.detail || {}; const ok = r.ok ? "ok" : "fail";
    let text = `${r.kind.replace("_", " ")}${r.dry_run ? " · dry-run" : ""}: `;
    if (r.kind === "discord") text += r.ok ? `posted to ${d.channel || "#loft-incident"}` : `failed · ${d.error || ""}`;
    else if (r.kind === "feed_submit") text += r.ok ? `tick ${d.tick_id} → ${d.action} · expected ${d.expected_action || d.expected || "?"} · score ${d.score ?? "?"}${d.false_alarm ? " · false alarm" : ""}` : `failed · ${d.error || ""}`;
    else if (r.kind === "one_action") text += r.ok ? (d.url ? "issue opened " : `issue validated for ${d.repo}`) : `failed · ${d.error || ""}`;
    else text += JSON.stringify(d).slice(0, 160);
    const div = document.createElement("div"); div.className = ok;
    div.innerHTML = esc(text) + (d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.url)}</a>` : "");
    $("action-results").appendChild(div); toast(text, ok);
  }

  function handle(ev) {
    if (ev.type === "hello") { state.team = ev.team; state.dryRun = ev.dry_run; return; }
    if (ev.type === "run.created") {
      if (!state.following && ev.run_id !== state.runId) return;
      state.runId = ev.run_id; reset(); state.facility = ev.facility || null;
      $("narrative").textContent = (ev.incident || {}).narrative || "";
      log(`run ${ev.run_id} · ${(ev.incident || {}).source}`, "", ev.t);
      step("classify"); setStatus("classifying", "busy"); refreshRuns(); return;
    }
    if (ev.run_id && ev.run_id !== state.runId) return;
    switch (ev.type) {
      case "run.status": setStatus(ev.status.replace("_", " "), ev.status === "done" ? "live" : ev.status === "failed" ? "warn" : "busy"); if (ev.status === "specialists") step("investigate"); if (ev.status === "deciding") { step("decide"); lane("commander", "investigating"); } log("status · " + ev.status, "", ev.t); break;
      case "facility": state.facility = ev.facility; break;
      case "component": mount(ev.component); if (!ev.component.provisional && ev.component.owner !== "system" && ev.component.owner !== "commander") step("compile"); break;
      case "component.remove": unmount(ev.key); break;
      case "component.invalid": log(`${ev.owner}: rejected a component (${ev.error})`, "warn", ev.t); break;
      case "signature": log("signature · " + (ev.matches || []).map((m) => `${m.label} ${m.score.toFixed(2)}`).join(", "), "", ev.t); break;
      case "classification": log(`classified ${ev.classification.incident_class} · severity ${ev.classification.severity} · ${ev.classification.people_at_risk} at risk`, "", ev.t); break;
      case "agent.status": lane(ev.agent, ev.state, ev.state === "done" ? `done · ${(ev.latency_ms / 1000).toFixed(1)}s · ${ev.tool_calls} tools` : ev.state === "compiling" ? "compiling" : "started"); break;
      case "agent.tool": lane(ev.agent, "investigating", `<b>${esc(ev.tool)}</b> ${esc((ev.input || "").slice(0, 60))}`); break;
      case "compile.done": log(`${ev.owner}: ${ev.count} components in ${ev.latency_ms} ms${ev.provisional ? " (provisional)" : ""}${ev.replayed ? " (cached)" : ""}`, "", ev.t); break;
      case "decision": showDecision(ev.decision); log(`decision · ${ev.decision.action} · ${ev.decision.rationale}`, "", ev.t); break;
      case "action": addActionResult(ev.result); break;
      case "metrics": showMetrics(ev); break;
      case "log": log(ev.text, ev.level, ev.t); break;
      case "selection": state.selected = ev.option_ids || []; break;
      default: break;
    }
  }
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { const p = $("pill-ws"); p.textContent = "connected"; p.className = "faint"; };
    ws.onclose = () => { const p = $("pill-ws"); p.textContent = "reconnecting"; p.className = "warn"; setTimeout(connect, 1500); };
    ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  }

  async function refreshRuns() {
    try {
      const runs = await api("/api/runs"); const sel = $("run-select"); const cur = sel.value;
      sel.innerHTML = '<option value="">recent runs</option>' + runs.slice().reverse().map((r) => `<option value="${r.run_id}">${esc(r.incident_id)}${r.tick_id != null ? " · t" + r.tick_id : ""} · ${esc(r.action || r.status)}</option>`).join("");
      sel.value = state.following ? "" : cur;
    } catch (e) { /* replay-only host */ }
  }
  async function loadRun(runId) {
    const run = await api(`/api/runs/${runId}`); state.following = false; state.runId = runId; reset(); state.facility = run.facility;
    $("narrative").textContent = run.incident.narrative;
    (run.components || []).slice().sort((a, b) => a.order - b.order).forEach(mount);
    renderSources(run.sources || []);
    if (run.decision) showDecision(run.decision);
    (run.actions || []).forEach(addActionResult);
    setStatus(run.status.replace("_", " "), run.status === "done" ? "live" : "busy");
    showMetrics({ timings: run.timings, llm_calls: run.llm_calls, sources: (run.sources || []).length, components: (run.components || []).length });
    (run.reports || []).forEach((r) => lane(r.agent, r.error ? "error" : "done", `done · ${(r.latency_ms / 1000).toFixed(1)}s · ${r.tool_calls} tools`));
    document.querySelectorAll("#stepper span").forEach((s) => (s.className = "done"));
  }
  function follow() { state.following = true; $("run-select").value = ""; }

  async function start(body) { follow(); try { await api("/api/incidents", body); } catch (e) { toast(e.message, "fail"); } }
  const SAMPLE = [
    ["kitchen-smoke", "Kitchen smoke, two ticks", "smoke with the alarm silent, then CO climbing"],
    ["medical-collapse", "Cardiac arrest at reception", "bystander CPR, AED location unknown"],
    ["district-power-outage", "District power outage", "120 attendees, emergency lighting only"],
    ["scheduled-alarm-test", "Scheduled alarm test", "a control case: nothing is wrong"],
    ["organizers/outbreak-level-5", "Organizers' outbreak fixture", "two ticks from the scenario feed"],
  ];
  async function loadMenu() {
    const data = await api("/api/fixtures");
    $("menu-fixtures").innerHTML = data.fixtures.filter((f) => f.group === "vigil").map((f) => `<button data-fixture="${esc(f.name)}" title="${esc(f.description)}">${esc(f.name)} <span class="faint">· ${f.ticks} tick${f.ticks === 1 ? "" : "s"}</span></button>`).join("");
    $("menu-organizers").innerHTML = data.fixtures.filter((f) => f.group === "organizers").map((f) => `<button data-fixture="${esc(f.name)}">${esc(f.name.replace("organizers/", ""))} <span class="faint">· ${f.ticks} tick${f.ticks === 1 ? "" : "s"}</span></button>`).join("");
    $("menu-replays").innerHTML = data.recordings.length ? data.recordings.map((r) => `<button data-replay="${esc(r.name)}">${esc(r.name)}</button>`).join("") : '<div class="menu-note">no recordings yet</div>';
    $("samples").innerHTML = SAMPLE.map(([f, t, s]) => `<button class="sample" data-fixture="${esc(f)}"><b>${esc(t)}</b><span>${esc(s)}</span></button>`).join("") + `<button class="sample" data-feed="outbreak-level-5"><b>Live feed</b><span>pull the next tick for ${esc(state.team || "your team")}</span></button>`;
    api("/api/scoreboard").then((sb) => { $("scoreboard").textContent = (sb.teams || []).slice(0, 6).map((t) => `${t.team} ${t.total_score}/${t.submissions}`).join(" · "); }).catch(() => {});
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) { if (!e.target.closest("#omnibar")) show("menu", false); return; }
    if (b.dataset.fixture) { show("menu", false); start({ source: "fixture", fixture: b.dataset.fixture, auto_act: $("auto-act").checked }); }
    else if (b.dataset.feed) { show("menu", false); start({ source: "feed", scenario: b.dataset.feed, auto_act: $("auto-act").checked }); }
    else if (b.dataset.replay) { show("menu", false); start({ source: "replay", recording: b.dataset.replay }); }
  });
  $("menu-btn").addEventListener("click", () => $("menu").classList.toggle("hidden"));
  $("btn-run").addEventListener("click", () => { const t = $("text-input").value.trim(); if (!t) { show("menu", true); return; } start({ source: "text", text: t, auto_act: $("auto-act").checked }); });
  $("text-input").addEventListener("input", (e) => { e.target.rows = Math.min(8, Math.max(1, e.target.value.split("\n").length)); });
  $("text-input").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") $("btn-run").click(); });
  $("run-select").addEventListener("change", (e) => { if (e.target.value) loadRun(e.target.value).catch((err) => toast(err.message, "fail")); else follow(); });
  $("btn-act").addEventListener("click", async () => {
    if (!state.runId) return; $("btn-act").disabled = true;
    try { const res = await api(`/api/runs/${state.runId}/act`, { action: $("override-action").value || null, dry_run: $("act-live").checked ? false : null }); if (!res.results.length) toast("observe: nothing to dispatch", ""); }
    catch (e) { toast(e.message, "fail"); $("btn-act").disabled = false; }
  });
  $("btn-recompile").addEventListener("click", async () => {
    if (!state.runId || !$("note-text").value.trim()) return; follow();
    try { await api(`/api/runs/${state.runId}/recompile`, { note: $("note-text").value.trim() }); $("note-text").value = ""; } catch (e) { toast(e.message, "fail"); }
  });

  const params = new URLSearchParams(location.search);
  const auto = params.get("replay") ? { source: "replay", recording: params.get("replay"), speed: Number(params.get("speed") || 1) } : params.get("fixture") ? { source: "fixture", fixture: params.get("fixture") } : null;
  if (auto) setTimeout(() => start(auto), 600);

  $("theme-btn").addEventListener("click", () => { const r = document.documentElement; const dark = r.dataset.theme !== "dark"; if (dark) r.dataset.theme = "dark"; else delete r.dataset.theme; try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch (e) {} });
  loadMenu().catch(() => {}); refreshRuns(); connect();
})();
