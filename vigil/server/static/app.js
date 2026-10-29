/* Vigil station. One WebSocket in; one state object; each section re-renders from state. */
(function () {
  const V = window.V;
  const { esc, pretty } = V;
  const $ = (id) => document.getElementById(id);
  const show = (id, on = true) => $(id).classList.toggle("hidden", !on);

  const AGENTS = [
    ["sensors", "Sensor analyst", "reads the sensors"],
    ["facility", "Facility lead", "walks the floor plan"],
    ["research", "Field researcher", "checks guidance on the web"],
    ["commander", "Commander", "makes the call"],
  ];
  const NAMES = Object.fromEntries(AGENTS.map(([k, n]) => [k, n]));
  NAMES.system = "Station";
  const TOOL = {
    current_readings: "read the sensors", signature_match: "compared the pattern with known hazards", sensor_trend: "checked how a reading changed",
    facility_graph: "read the floor plan", route_to_exit: "worked out a route to an exit", venue_lookup: "looked up the venue",
    web_search: "searched the web", read_page: "read a page", research_brief: "pulled a cited research brief", local_conditions: "checked conditions outside",
  };
  const STEPS = ["classify", "investigate", "compile", "decide", "act"];
  const FRIENDLY = {
    "kitchen-smoke": ["Kitchen smoke", "Smoke at the coffee bar, alarm silent. Two updates."],
    "medical-collapse": ["Cardiac arrest", "Someone collapsed at reception."],
    "district-power-outage": ["Power outage", "Whole floor dark, 120 people inside."],
    "network-outage": ["Network down", "Core switch lost power, doors failed open."],
    "scheduled-alarm-test": ["Alarm test", "Announced test. Nothing is wrong."],
    "organizers/outbreak-level-5": ["Organizers: outbreak", "Two ticks from the scenario feed."],
    "organizers/control-tick": ["Organizers: control tick", "A harmless reading."],
    "organizers/haunted-dashboard": ["Organizers: raw log", "One unstructured incident report."],
  };

  const blank = () => ({
    runId: null, replay: false, incident: null, facility: null, comps: new Map(), sources: [], decision: null, choice: null,
    agents: {}, activity: [], results: [], selected: [], checked: new Set(), step: null, status: "idle", metrics: null, why: false,
  });
  let S = blank();
  let following = true, team = "", tab = "do", raf = 0, lastMap = "", lastTab = "";

  /* ---------------------------------------------------------------- utils */
  function toast(text, cls) { const t = document.createElement("div"); t.className = "toast " + (cls || ""); t.textContent = text; $("toasts").appendChild(t); setTimeout(() => t.remove(), 5000); }
  async function api(path, body) {
    const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    if (!res.ok) { let m = res.statusText; try { m = (await res.json()).detail || m; } catch (e) {} throw new Error(m); }
    return res.json();
  }
  const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); };
  function byType() {
    const T = {};
    [...S.comps.values()].sort((a, b) => a.order - b.order).forEach((c) => (T[c.component] = T[c.component] || []).push(c));
    return T;
  }
  const people = (s) => { const m = String(s || "").match(/(\d+)/); return m ? +m[1] : V.AT_RISK.test(s) ? 1 : 0; };

  /* ---------------------------------------------------------------- render */
  function render() {
    const on = !!S.incident;
    show("welcome", !on); show("view", on);
    if (!on) return;
    const T = byType();
    renderHero(T); renderProgress(); renderCall(T); renderMap(T); renderTabs(T);
  }

  function renderHero(T) {
    const h = (T.IncidentHeader || [])[0], b = (T.SeverityBanner || [])[0], inc = S.incident;
    $("meta").textContent = [inc.incident_id, inc.tick_id != null ? `tick ${inc.tick_id}` : "", inc.source, S.replay ? "replay" : ""].filter(Boolean).join(" · ");
    $("title").textContent = h && h.props.severity ? h.props.title.replace(/_/g, " ") : "Reading the log…";
    let chips = "";
    if (h && h.props.severity) {
      const sev = h.props.severity, cls = sev >= 4 ? "danger" : sev === 3 ? "warn" : "info";
      chips += `<span class="chip">${pretty(h.props.incident_class)}</span><span class="chip ${cls}">Severity ${sev}/5${b ? " · " + esc(b.props.label) : ""}</span>`;
    }
    if (b) chips += `<span class="chip ${b.props.people_at_risk ? "danger" : "ok"}">${b.props.people_at_risk ? `${b.props.people_at_risk} ${b.props.people_at_risk === 1 ? "person" : "people"} at risk` : "Nobody at risk"}</span>`;
    if (S.metrics && S.metrics.timings && S.metrics.timings.decided_ms) chips += `<span class="chip ghost">decided in ${(S.metrics.timings.decided_ms / 1000).toFixed(0)}s · ${S.metrics.sources} sources</span>`;
    $("chips").innerHTML = chips;
  }

  function renderProgress() {
    const j = STEPS.indexOf(S.step);
    document.querySelectorAll("#progress li").forEach((li) => { const i = STEPS.indexOf(li.dataset.step); li.className = j < 0 ? "" : i < j ? "done" : i === j ? (S.step === "act" && S.results.length ? "done" : "now") : ""; });
  }

  function renderCall(T) {
    const d = S.decision || ((T.DecisionPanel || [])[0] || {}).props;
    show("call-wait", !d); show("call-ready", !!d);
    if (!d) {
      $("wait-text").textContent = S.status === "failed" ? "The run failed. Check the Activity tab." : S.step === "decide" ? "The commander is weighing the three reports." : S.step ? "Three specialists are investigating. The recommended call appears here." : "Starting…";
      $("crew").innerHTML = AGENTS.map(([k, n, role]) => { const a = S.agents[k] || {}; return `<div class="mate ${a.state || "idle"}"><span class="av ${k}">${n[0]}</span><div><b>${n}</b><span>${esc(a.last || role)}</span></div></div>`; }).join("");
      $("call").dataset.action = "";
      return;
    }
    const proposed = d.action;
    if (!S.choice) S.choice = proposed;
    $("call").dataset.action = S.choice;
    $("call-action").textContent = proposed;
    $("call-conf").textContent = `${Math.round((d.confidence || 0) * 100)}% confident`;
    $("call-why").textContent = V.firstSentence(d.rationale).replace(/_/g, " ");
    $("why").innerHTML = `<p>${esc(d.rationale)}</p>${(d.alternatives || []).map((a) => `<p><b>Not ${esc(a.action)}:</b> ${esc(a.why_not)}</p>`).join("")}`;
    show("why", S.why);
    $("why-btn").textContent = S.why ? "Hide reasoning" : "Why, and why not the others";
    if (S.decision && document.activeElement !== $("dispatch")) {
      let msg = S.decision.dispatch_message || "";
      if (S.choice !== proposed) { const p = msg.split(" · "); if (p.length >= 3) { p[2] = S.choice; msg = p.join(" · "); } }
      if ($("dispatch").dataset.src !== msg) { $("dispatch").value = msg; $("dispatch").dataset.src = msg; }
    }
    document.querySelectorAll("#seg button").forEach((b) => b.classList.toggle("on", b.dataset.act === S.choice));
    const btn = $("btn-act");
    if (S.replay) { btn.disabled = true; btn.textContent = "Replay: actions are off"; }
    else if (!S.decision) { btn.disabled = true; btn.textContent = "Finishing…"; }
    else if (S.results.length) { btn.disabled = false; btn.textContent = "Send again"; }
    else { btn.disabled = false; btn.textContent = S.choice === "observe" ? "Confirm: keep watching" : S.choice !== proposed ? `Override: ${S.choice}` : "Approve & send alert"; }
    $("results").innerHTML = S.results.map((r) => {
      const dd = r.detail || {}; let t;
      if (r.kind === "discord") t = r.ok ? `Alert ${r.dry_run ? "ready (dry run)" : "posted"} to #loft-incident` : `Alert failed: ${dd.error || ""}`;
      else if (r.kind === "feed_submit") t = r.ok ? `Feed: tick ${dd.tick_id} scored ${dd.score ?? "?"}${dd.false_alarm ? " (false alarm)" : ""}` : `Feed failed: ${dd.error || ""}`;
      else if (r.kind === "one_action") t = r.ok ? (dd.url ? "Review filed in GitHub" : "Review checked (dry run)") : `Review failed: ${dd.error || ""}`;
      else t = r.kind;
      return `<div class="${r.ok ? "ok" : "fail"}">${r.ok ? "✓" : "✕"} ${esc(t)}${dd.url ? ` <a href="${esc(dd.url)}" target="_blank" rel="noopener">open</a>` : ""}</div>`;
    }).join("");
  }

  function mapData(T) {
    const fm = (T.FacilityMap || [])[0], d = { hot: [], iso: [], safe: [], readings: {}, people: {}, routes: [] };
    if (fm) { d.hot = fm.props.highlight_sectors; d.iso = fm.props.isolated_sectors; d.safe = fm.props.safe_sectors; }
    else (S.incident.sector_reports || []).forEach((r) => { if (r.containment_state === "breached") d.hot.push(r.sector); else if (r.containment_state === "degraded") d.iso.push(r.sector); });
    (T.SensorPanel || []).forEach((p) => { d.readings[p.props.sector] = p.props.readings.filter((r) => r.status !== "ok").sort((a, b) => (a.status === "danger" ? -1 : 1)); });
    const pr = (T.PeopleAtRisk || [])[0];
    if (pr) pr.props.groups.forEach((g) => (d.people[g.sector] = { count: g.count, risk: V.AT_RISK.test(g.status) }));
    else (S.incident.sector_reports || []).forEach((r) => { const n = people(r.civilian_status); if (n) d.people[r.sector] = { count: n, risk: V.AT_RISK.test(r.civilian_status) }; });
    const er = (T.EvacuationRoute || [])[0];
    if (er) d.routes = er.props.routes;
    return { d, caption: fm ? fm.props.caption : "" };
  }

  function renderMap(T) {
    const { d, caption } = mapData(T);
    $("map-title").textContent = S.facility ? `${S.facility.name}${S.facility.floor && S.facility.floor !== "unknown floor" ? " · " + S.facility.floor : ""}` : "Floor plan";
    const html = V.mapSVG(S.facility, d);
    if (html !== lastMap) { $("map").innerHTML = html; lastMap = html; }
    $("map-cap").textContent = caption;
  }

  function renderTabs(T) {
    const n = {
      do: ((T.Runbook || [])[0] || { props: { steps: [] } }).props.steps.length || ((T.MonitorPanel || []).length ? 1 : 0),
      options: ((T.ResponseOptions || [])[0] || { props: { options: [] } }).props.options.length,
      readings: (T.SensorPanel || []).length, people: ((T.PeopleAtRisk || [])[0] || { props: { groups: [] } }).props.groups.length,
      activity: S.activity.length, sources: S.sources.length,
    };
    const prov = { readings: (T.SensorPanel || []).some((c) => c.provisional), people: (T.PeopleAtRisk || []).some((c) => c.provisional), options: (T.ResponseOptions || []).some((c) => c.provisional) };
    Object.entries(n).forEach(([k, v]) => { $("n-" + k).textContent = v || ""; $("n-" + k).className = prov[k] ? "prov" : ""; });
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    let html = "";
    if (tab === "do") html = V.renderDo(T);
    else if (tab === "options") html = V.renderOptions(T, S.selected);
    else if (tab === "readings") html = V.renderReadings(T);
    else if (tab === "people") html = V.renderPeople(T);
    else if (tab === "activity") html = V.renderActivity(S.activity);
    else if (tab === "sources") html = V.renderSources(S.sources);
    const key = tab + html;
    if (key !== lastTab) {
      const body = $("tab-body"), keep = body.scrollTop;
      body.innerHTML = html; lastTab = key;
      if (tab !== "activity") body.scrollTop = keep; else body.scrollTop = body.scrollHeight;
      body.querySelectorAll("input[data-step]").forEach((i) => { i.checked = S.checked.has(i.dataset.step); });
    }
    $("btn-update").disabled = S.replay || !S.decision;
    $("note").disabled = S.replay || !S.decision;
    $("note").placeholder = S.replay ? "Updates are off in a replay" : !S.decision ? "Available once there is a call" : "What changed? e.g. oven power cut, smoke thinning";
  }

  function activity(agent, html, t) {
    S.activity.push({ agent, name: NAMES[agent] || agent, html, t });
    if (S.agents[agent] !== undefined || NAMES[agent]) S.agents[agent] = { ...(S.agents[agent] || {}), last: html.replace(/<[^>]+>/g, "") };
  }

  /* ---------------------------------------------------------------- events */
  function reset(ev) {
    S = blank();
    S.runId = ev.run_id; S.replay = !!ev.replayed; S.incident = ev.incident || {}; S.facility = ev.facility || null;
    lastMap = ""; lastTab = ""; tab = "do"; S.step = "classify";
    $("dispatch").dataset.src = ""; $("note").value = "";
    if (S.facility && S.facility.id === "aws-builder-loft-sf") api("/api/facility/aws-builder-loft-sf").then((f) => { S.facility = f; lastMap = ""; schedule(); }).catch(() => {});
  }

  function handle(ev) {
    if (ev.type === "hello") { team = ev.team; $("team").textContent = team; return; }
    if (ev.type === "run.created") {
      if (!following && ev.run_id !== S.runId) return;
      reset(ev); refreshRuns(); schedule(); return;
    }
    if (ev.run_id && ev.run_id !== S.runId) return;
    switch (ev.type) {
      case "run.status":
        S.status = ev.status;
        if (ev.status === "specialists") S.step = "investigate";
        if (ev.status === "deciding") { S.step = "decide"; S.agents.commander = { state: "investigating", last: "weighing the reports" }; }
        if (ev.status === "awaiting_operator" || ev.status === "acting" || ev.status === "done") S.step = "act";
        if (ev.status === "failed") activity("system", "the run failed", ev.t);
        document.body.classList.toggle("busy", !["awaiting_operator", "done", "failed"].includes(ev.status));
        break;
      case "facility": S.facility = ev.facility; break;
      case "component": S.comps.set(ev.component.key, ev.component); if (!ev.component.provisional && ["sensors", "facility", "research"].includes(ev.component.owner) && S.step === "investigate") S.step = "compile"; break;
      case "component.remove": S.comps.delete(ev.key); break;
      case "classification": { const c = ev.classification; activity("system", `classified it as <b>${pretty(c.incident_class)}</b>, severity ${c.severity}/5`, ev.t); break; }
      case "agent.status":
        S.agents[ev.agent] = { ...(S.agents[ev.agent] || {}), state: ev.state };
        activity(ev.agent, ev.state === "done" ? `finished in ${Math.round(ev.latency_ms / 1000)}s` : ev.state === "compiling" ? "is writing up its findings" : "started", ev.t);
        break;
      case "agent.tool": S.agents[ev.agent] = { ...(S.agents[ev.agent] || {}), state: "investigating" }; activity(ev.agent, `${TOOL[ev.tool] || esc(ev.tool)}${ev.input ? ` <i>${esc(ev.input.slice(0, 80))}</i>` : ""}`, ev.t); break;
      case "decision": S.decision = ev.decision; S.choice = ev.decision.action; S.agents.commander = { state: "done", last: `recommends ${ev.decision.action}` }; activity("commander", `recommends <b>${esc(ev.decision.action)}</b>${ev.decision.guardrail_note ? ` (policy: ${esc(ev.decision.guardrail_note)})` : ""}`, ev.t); S.step = "act"; break;
      case "action": if (!S.results.some((r) => r.kind === ev.result.kind && r.ts === ev.result.ts)) S.results.push(ev.result); break;
      case "metrics": S.metrics = ev; break;
      case "component.invalid": activity("system", `rejected a malformed ${esc(ev.owner)} component`, ev.t); break;
      case "log": if (ev.level !== "info") activity("system", esc(ev.text), ev.t); break;
      case "selection": S.selected = ev.option_ids || []; break;
      default: break;
    }
    if (ev.type === "component" && ev.component.component === "Sources") S.sources = ev.component.props.sources || [];
    schedule();
  }

  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { $("conn").className = "conn on"; $("conn").title = "connected"; };
    ws.onclose = () => { $("conn").className = "conn off"; $("conn").title = "reconnecting"; setTimeout(connect, 1500); };
    ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  }

  /* ---------------------------------------------------------------- sidebar */
  async function refreshRuns() {
    try {
      const runs = (await api("/api/runs")).slice().reverse();
      $("runs").innerHTML = runs.length ? runs.slice(0, 8).map((r) => `<button class="item run ${r.run_id === S.runId ? "on" : ""}" data-run="${r.run_id}"><span class="it-t">${esc(r.incident_class ? pretty(r.incident_class) : r.incident_id)}${r.tick_id != null ? ` <i>t${r.tick_id}</i>` : ""}</span><span class="it-s">${r.action ? `<em class="act ${esc(r.action)}">${esc(r.action)}</em>` : esc(r.status.replace("_", " "))}</span></button>`).join("") : `<div class="list-empty">Nothing yet. Pick an incident below.</div>`;
    } catch (e) { /* replay-only host */ }
  }
  async function loadRun(id) {
    const run = await api(`/api/runs/${id}`);
    following = false;
    S = blank(); S.runId = id; S.incident = run.incident; S.facility = run.facility; lastMap = ""; lastTab = "";
    (run.components || []).forEach((c) => S.comps.set(c.key, c));
    S.sources = run.sources || []; S.decision = run.decision; S.choice = run.decision ? run.decision.action : null;
    S.results = run.actions || []; S.status = run.status; S.step = run.decision ? "act" : "investigate";
    S.metrics = { timings: run.timings, sources: (run.sources || []).length };
    (run.reports || []).forEach((r) => (S.agents[r.agent] = { state: r.error ? "error" : "done", last: `finished, ${r.tool_calls} tool calls` }));
    refreshRuns(); render();
  }
  async function loadSide() {
    const data = await api("/api/fixtures");
    const item = (f) => { const [t, s] = FRIENDLY[f.name] || [f.name, f.description]; return `<button class="item" data-fixture="${esc(f.name)}"><span class="it-t">${esc(t)}</span><span class="it-s">${esc(s)}</span></button>`; };
    $("fixtures").innerHTML = data.fixtures.map(item).join("");
    $("replays").innerHTML = data.recordings.length ? data.recordings.slice(0, 6).map((r) => `<button class="item" data-replay="${esc(r.name)}"><span class="it-t">${esc(r.name.replace(/-tick-/, " · tick ").replace(/-/g, " "))}</span><span class="it-s">no network needed</span></button>`).join("") : `<div class="list-empty">No recordings yet.</div>`;
    $("sample-cards").innerHTML = ["kitchen-smoke", "medical-collapse", "district-power-outage", "scheduled-alarm-test"].map((k) => `<button class="card-btn" data-fixture="${k}"><b>${esc(FRIENDLY[k][0])}</b><span>${esc(FRIENDLY[k][1])}</span></button>`).join("");
  }

  /* ---------------------------------------------------------------- actions */
  async function start(body) { following = true; try { await api("/api/incidents", { auto_act: $("auto-act").checked, ...body }); } catch (e) { toast(e.message, "fail"); } }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.fixture) start({ source: "fixture", fixture: b.dataset.fixture });
    else if (b.dataset.feed) start({ source: "feed", scenario: b.dataset.feed });
    else if (b.dataset.replay) start({ source: "replay", recording: b.dataset.replay, speed: 1.5 });
    else if (b.dataset.run) loadRun(b.dataset.run).catch((err) => toast(err.message, "fail"));
    else if (b.dataset.tab) { tab = b.dataset.tab; render(); }
    else if (b.dataset.act) { S.choice = b.dataset.act; render(); }
    else if (b.dataset.cite) { tab = "sources"; render(); const n = $("src-" + b.dataset.cite); if (n) { n.classList.add("hl"); n.scrollIntoView({ block: "center", behavior: "smooth" }); setTimeout(() => n.classList.remove("hl"), 1800); } }
  });
  $("tab-body").addEventListener("change", (e) => {
    const i = e.target;
    if (i.dataset.step) { i.checked ? S.checked.add(i.dataset.step) : S.checked.delete(i.dataset.step); return; }
    if (i.closest(".opts")) { S.selected = [...$("tab-body").querySelectorAll(".opts input:checked")].map((x) => x.value); if (S.runId && !S.replay) api(`/api/runs/${S.runId}/select`, { option_ids: S.selected }).catch(() => {}); }
  });
  $("why-btn").addEventListener("click", () => { S.why = !S.why; render(); });
  $("btn-run").addEventListener("click", () => { const t = $("text-input").value.trim(); if (!t) { $("text-input").focus(); toast("Paste a log first, or pick an incident on the left.", ""); return; } start({ source: "text", text: t }); $("text-input").value = ""; $("text-input").rows = 1; });
  $("text-input").addEventListener("input", (e) => { e.target.rows = Math.min(6, Math.max(1, e.target.value.split("\n").length)); });
  $("text-input").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") $("btn-run").click(); });
  $("btn-act").addEventListener("click", async () => {
    if (!S.runId || !S.decision || S.replay) return;
    const btn = $("btn-act"); btn.disabled = true; btn.textContent = "Sending…";
    try {
      const res = await api(`/api/runs/${S.runId}/act`, { action: S.choice !== S.decision.action ? S.choice : null, dry_run: $("act-live").checked ? false : null });
      res.results.forEach((r) => { if (!S.results.some((x) => x.kind === r.kind && x.ts === r.ts)) S.results.push(r); });
      if (!res.results.length) toast("Observe: nothing to send. The station keeps watching.", "");
      refreshRuns();
    } catch (err) { toast(err.message, "fail"); }
    render();
  });
  async function update() {
    const note = $("note").value.trim();
    if (!note || !S.runId || S.replay) return;
    following = true;
    try { await api(`/api/runs/${S.runId}/recompile`, { note }); $("note").value = ""; } catch (err) { toast(err.message, "fail"); }
  }
  $("btn-update").addEventListener("click", update);
  $("note").addEventListener("keydown", (e) => { if (e.key === "Enter") update(); });

  $("help-btn").addEventListener("click", () => show("help", true));
  $("help-close").addEventListener("click", () => show("help", false));
  $("help").addEventListener("click", (e) => { if (e.target.id === "help") show("help", false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") show("help", false); if (e.key === "?" && !["TEXTAREA", "INPUT"].includes(document.activeElement.tagName)) show("help", true); });
  $("theme-btn").addEventListener("click", () => { const r = document.documentElement, dark = r.dataset.theme !== "dark"; if (dark) r.dataset.theme = "dark"; else delete r.dataset.theme; try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch (e) {} });

  const q = new URLSearchParams(location.search);
  if (q.get("replay")) setTimeout(() => start({ source: "replay", recording: q.get("replay"), speed: Number(q.get("speed") || 1.5) }), 500);
  else if (q.get("fixture")) setTimeout(() => start({ source: "fixture", fixture: q.get("fixture") }), 500);
  try { if (!localStorage.getItem("seen-help")) { show("help", true); localStorage.setItem("seen-help", "1"); } } catch (e) {}

  loadSide().catch(() => {}); refreshRuns(); connect(); render();
})();
