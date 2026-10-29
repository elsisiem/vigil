/* Vigil frontend: one WebSocket in, components out. No build step, no network beyond the server. */
(function () {
  const { render, esc } = window.VigilComponents;
  const $ = (id) => document.getElementById(id);

  const state = {
    team: "", dryRun: true, following: true, runId: null, facility: null,
    components: new Map(), cards: new Map(), sources: [], routes: [], selected: [], decision: null, status: "idle",
  };

  // ------------------------------------------------------------------ helpers
  function toast(text, cls) {
    const t = document.createElement("div");
    t.className = "toast " + (cls || "");
    t.textContent = text;
    $("toasts").appendChild(t);
    setTimeout(() => t.remove(), 6000);
  }
  function timeline(text, level, t) {
    const d = document.createElement("div");
    d.className = level || "";
    d.innerHTML = `<span class="t">${t != null ? Number(t).toFixed(1) + "s" : ""}</span>${esc(text)}`;
    const tl = $("timeline");
    tl.appendChild(d);
    tl.scrollTop = tl.scrollHeight;
  }
  function setPill(id, text, cls) {
    const p = $(id);
    p.textContent = text;
    p.className = "pill " + (cls || "");
  }
  async function api(path, body) {
    const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    if (!res.ok) throw new Error((await res.text()) || res.statusText);
    return res.json();
  }

  // ------------------------------------------------------------------ rendering
  const ctx = {
    get facility() { return state.facility; },
    get routes() { return state.routes; },
    get selected() { return state.selected; },
    select(ids) {
      state.selected = ids;
      if (state.runId) api(`/api/runs/${state.runId}/select`, { option_ids: ids }).catch(() => {});
    },
  };

  function resetStage() {
    state.components.clear();
    state.cards.clear();
    state.sources = [];
    state.routes = [];
    state.selected = [];
    state.decision = null;
    $("grid").innerHTML = "";
    $("sources").innerHTML = '<span class="muted">Waiting for grounding calls…</span>';
    $("console").classList.add("hidden");
    $("console-empty").classList.remove("hidden");
    $("action-results").innerHTML = "";
    $("metrics").innerHTML = '<span class="muted">—</span>';
    document.querySelectorAll(".agent").forEach((a) => { a.className = "agent"; a.querySelector(".state").textContent = "idle"; a.querySelector(".agent-body").innerHTML = ""; });
    $("timeline").innerHTML = "";
  }

  function upsertComponent(comp) {
    state.components.set(comp.key, comp);
    if (comp.component === "Sources") { renderSources(comp.props.sources || []); return; }
    if (comp.component === "EvacuationRoute") { state.routes = comp.props.routes || []; const fm = [...state.components.values()].find((c) => c.component === "FacilityMap"); if (fm) upsertComponent(fm); }
    const node = render(comp, ctx);
    if (!node) return;
    node.dataset.key = comp.key;
    node.style.order = comp.order || 0;
    if (comp.provisional) node.classList.add("provisional");
    const existing = state.cards.get(comp.key);
    if (existing) { existing.replaceWith(node); node.classList.add("updated"); } else { $("grid").appendChild(node); }
    state.cards.set(comp.key, node);
    node.querySelectorAll("[data-cite]").forEach((c) => c.addEventListener("click", () => highlightSource(c.dataset.cite)));
  }

  function renderSources(sources) {
    state.sources = sources;
    const box = $("sources");
    if (!sources.length) { box.innerHTML = '<span class="muted">No grounding calls yet.</span>'; return; }
    box.innerHTML = sources.map((s) => `<div class="src" id="src-${esc(s.id)}"><span class="id">${esc(s.id)}</span><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a><span class="kind">${esc(s.kind)}</span><div class="sn">${esc(s.snippet)}</div></div>`).join("");
  }
  function highlightSource(id) {
    document.querySelectorAll(".src.hl").forEach((s) => s.classList.remove("hl"));
    const node = $("src-" + id);
    if (node) { node.classList.add("hl"); node.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  }

  function setAgent(agent, stateName, extraHtml) {
    const node = document.querySelector(`.agent[data-agent="${agent}"]`);
    if (!node) return;
    node.className = "agent " + stateName;
    node.querySelector(".state").textContent = stateName;
    if (extraHtml) { const b = node.querySelector(".agent-body"); const d = document.createElement("div"); d.innerHTML = extraHtml; b.appendChild(d); b.scrollTop = b.scrollHeight; }
  }

  function showDecision(d) {
    state.decision = d;
    $("console-empty").classList.add("hidden");
    $("console").classList.remove("hidden");
    const chip = $("console-action");
    chip.textContent = d.action;
    chip.className = "action-chip " + d.action;
    $("dispatch-text").value = d.dispatch_message || "";
    $("override-action").value = "";
    $("btn-act").disabled = false;
    setAgent("commander", "done", `decided ${d.action} (${Math.round(d.confidence * 100)}%)`);
    if (d.guardrail_note) timeline("guardrail: " + d.guardrail_note, "warn");
  }

  function showMetrics(m) {
    const t = m.timings || {};
    const llm = m.llm || {};
    $("metrics").innerHTML = [
      ["first component", t.first_component_ms != null ? t.first_component_ms + " ms" : "—"],
      ["classified", t.classified_ms != null ? t.classified_ms + " ms" : "—"],
      ["specialists", t.specialists_ms != null ? t.specialists_ms + " ms" : "—"],
      ["decided", t.decided_ms != null ? t.decided_ms + " ms" : "—"],
      ["model calls", m.llm_calls ?? "—"],
      ["sources", m.sources ?? "—"],
      ["components", `${m.components ?? "—"}${m.invalid_components ? ` (${m.invalid_components} rejected)` : ""}`],
      ["p50 model latency", llm.p50_latency_ms != null ? llm.p50_latency_ms + " ms" : "—"],
    ].map(([k, v]) => `<div><b>${esc(v)}</b><br><span>${esc(k)}</span></div>`).join("");
  }

  function addActionResult(r) {
    const ok = r.ok ? "ok" : "fail";
    const d = r.detail || {};
    let text = `${r.kind}${r.dry_run ? " (dry-run)" : ""}: `;
    if (r.kind === "discord") text += r.ok ? `posted to ${d.channel || "#loft-incident"}` : `failed — ${d.error || ""}`;
    else if (r.kind === "feed_submit") text += r.ok ? `tick ${d.tick_id} → ${d.action}; expected ${d.expected_action || d.expected || "?"}; score ${d.score ?? "?"}${d.false_alarm ? " · FALSE ALARM" : ""}` : `failed — ${d.error || ""}`;
    else if (r.kind === "one_action") text += r.ok ? (d.url ? `issue opened ${d.url}` : `issue validated for ${d.repo}`) : `failed — ${d.error || ""}`;
    else text += JSON.stringify(d).slice(0, 160);
    const div = document.createElement("div");
    div.className = ok;
    div.innerHTML = d.url ? `${esc(text.split(d.url)[0])}<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.url)}</a>` : esc(text);
    $("action-results").appendChild(div);
    toast(text, ok);
  }

  // ------------------------------------------------------------------ events
  function handle(ev) {
    if (ev.type === "hello") { state.team = ev.team; state.dryRun = ev.dry_run; setPill("pill-team", "team " + ev.team); setPill("pill-mode", ev.offline ? "offline replay" : ev.dry_run ? "dry-run actions" : "live actions", ev.dry_run ? "warn" : "live"); return; }
    if (ev.type === "run.created") {
      if (!state.following && ev.run_id !== state.runId) return;
      state.runId = ev.run_id;
      resetStage();
      state.facility = ev.facility || null;
      const inc = ev.incident || {};
      $("narrative").textContent = `${inc.incident_id || ""}${inc.tick_id != null ? " · tick " + inc.tick_id : ""} · ${inc.source || ""}${ev.replayed ? " · replay" : ""}\n${inc.narrative || ""}`;
      timeline(`run ${ev.run_id} started (${inc.source})`, "", ev.t);
      refreshRuns();
      return;
    }
    if (ev.run_id && ev.run_id !== state.runId) return;
    switch (ev.type) {
      case "run.status": state.status = ev.status; setPill("pill-status", ev.status, ev.status === "done" ? "live" : ev.status === "failed" ? "warn" : "busy"); if (ev.status === "deciding") setAgent("commander", "investigating"); timeline("status → " + ev.status, "", ev.t); break;
      case "facility": state.facility = ev.facility; break;
      case "component": upsertComponent(ev.component); break;
      case "component.invalid": timeline(`${ev.owner}: rejected a component (${ev.error})`, "warn", ev.t); break;
      case "signature": timeline("signature: " + (ev.matches || []).map((m) => `${m.label} ${m.score.toFixed(2)}`).join(", "), "", ev.t); break;
      case "classification": timeline(`classified ${ev.classification.incident_class} · severity ${ev.classification.severity} · ${ev.classification.people_at_risk} at risk${ev.classification.is_control ? " · control" : ""}`, "", ev.t); break;
      case "agent.status": setAgent(ev.agent, ev.state, ev.state === "done" ? `done in ${ev.latency_ms} ms · ${ev.tool_calls} tool calls · ${(ev.components || []).join(", ")}` : ev.state === "compiling" ? "compiling components…" : "investigating…"); break;
      case "agent.tool": setAgent(ev.agent, "investigating", `<b>${esc(ev.tool)}</b>(${esc(ev.input || "")})`); break;
      case "compile.done": timeline(`${ev.owner}: ${ev.count} components in ${ev.latency_ms} ms${ev.replayed ? " (cached)" : ""}`, "", ev.t); break;
      case "decision": showDecision(ev.decision); timeline(`decision: ${ev.decision.action} — ${ev.decision.rationale}`, "", ev.t); break;
      case "action": addActionResult(ev.result); break;
      case "metrics": showMetrics(ev); break;
      case "log": timeline(ev.text, ev.level, ev.t); break;
      case "selection": state.selected = ev.option_ids || []; break;
      default: break;
    }
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => setPill("pill-ws", "connected", "live");
    ws.onclose = () => { setPill("pill-ws", "reconnecting…", "warn"); setTimeout(connect, 1500); };
    ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  }

  // ------------------------------------------------------------------ run switching
  async function refreshRuns() {
    try {
      const runs = await api("/api/runs");
      const sel = $("run-select");
      const cur = sel.value;
      sel.innerHTML = '<option value="">(follow latest)</option>' + runs.slice().reverse().map((r) => `<option value="${r.run_id}">${esc(r.incident_id)}${r.tick_id != null ? " · t" + r.tick_id : ""} · ${esc(r.status)}${r.action ? " · " + r.action : ""}</option>`).join("");
      sel.value = state.following ? "" : cur;
    } catch (e) { /* server may be a replay-only host */ }
  }
  async function loadRun(runId) {
    const run = await api(`/api/runs/${runId}`);
    state.following = false;
    state.runId = runId;
    resetStage();
    state.facility = run.facility;
    $("narrative").textContent = `${run.incident.incident_id}${run.incident.tick_id != null ? " · tick " + run.incident.tick_id : ""} · ${run.incident.source}\n${run.incident.narrative}`;
    (run.components || []).slice().sort((a, b) => a.order - b.order).forEach(upsertComponent);
    renderSources(run.sources || []);
    if (run.decision) showDecision(run.decision);
    (run.actions || []).forEach(addActionResult);
    setPill("pill-status", run.status, run.status === "done" ? "live" : "busy");
    showMetrics({ timings: run.timings, llm_calls: run.llm_calls, sources: (run.sources || []).length, components: (run.components || []).length });
    (run.reports || []).forEach((r) => setAgent(r.agent, r.error ? "error" : "done", `done in ${r.latency_ms} ms · ${r.tool_calls} tool calls`));
  }

  // ------------------------------------------------------------------ controls
  async function loadFixtures() {
    const data = await api("/api/fixtures");
    const sel = $("fixture-select");
    const groups = { vigil: "Vigil incidents", organizers: "Organizer fixtures" };
    sel.innerHTML = Object.keys(groups).map((g) => `<optgroup label="${groups[g]}">` + data.fixtures.filter((f) => f.group === g).map((f) => `<option value="${esc(f.name)}" title="${esc(f.description)}">${esc(f.name)} (${f.ticks} tick${f.ticks === 1 ? "" : "s"})</option>`).join("") + "</optgroup>").join("");
    const rs = $("replay-select");
    rs.innerHTML = data.recordings.length ? data.recordings.map((r) => `<option value="${esc(r.name)}">${esc(r.name)} · ${esc(r.incident_id || "")}</option>`).join("") : '<option value="">(no recordings yet)</option>';
  }
  function follow() { state.following = true; $("run-select").value = ""; }

  document.querySelectorAll("#start-tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#start-tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab-body").forEach((x) => x.classList.toggle("hidden", x.id !== "tab-" + b.dataset.tab));
    if (b.dataset.tab === "feed") api("/api/scoreboard").then((sb) => { $("scoreboard").innerHTML = (sb.teams || []).slice(0, 8).map((t) => `${esc(t.team)} ${t.total_score}/${t.submissions}${t.false_alarms ? " ⚠" + t.false_alarms : ""}`).join(" · "); }).catch(() => {});
  }));
  $("btn-run-fixture").addEventListener("click", async () => { follow(); try { await api("/api/incidents", { source: "fixture", fixture: $("fixture-select").value, auto_act: $("auto-act").checked }); } catch (e) { toast(e.message, "fail"); } });
  $("btn-run-text").addEventListener("click", async () => { follow(); try { await api("/api/incidents", { source: "text", text: $("text-input").value, auto_act: $("auto-act").checked }); } catch (e) { toast(e.message, "fail"); } });
  $("btn-run-feed").addEventListener("click", async () => { follow(); try { await api("/api/incidents", { source: "feed", scenario: $("feed-scenario").value, team: $("feed-team").value || null, auto_act: $("auto-act").checked }); } catch (e) { toast(e.message, "fail"); } });
  $("btn-replay").addEventListener("click", async () => { follow(); try { await api("/api/incidents", { source: "replay", recording: $("replay-select").value }); } catch (e) { toast(e.message, "fail"); } });
  $("run-select").addEventListener("change", (e) => { if (e.target.value) loadRun(e.target.value).catch((err) => toast(err.message, "fail")); else follow(); });

  $("btn-act").addEventListener("click", async () => {
    if (!state.runId) return;
    $("btn-act").disabled = true;
    try {
      const res = await api(`/api/runs/${state.runId}/act`, { action: $("override-action").value || null, dry_run: $("act-live").checked ? false : null });
      if (!res.results.length) toast("nothing to do (observe)", "");
    } catch (e) { toast(e.message, "fail"); $("btn-act").disabled = false; }
  });
  $("btn-recompile").addEventListener("click", async () => {
    if (!state.runId || !$("note-text").value.trim()) return;
    follow();
    try { await api(`/api/runs/${state.runId}/recompile`, { note: $("note-text").value.trim() }); $("note-text").value = ""; } catch (e) { toast(e.message, "fail"); }
  });

  // URL hooks for demos and offline replay: ?replay=<recording> or ?fixture=<name>
  const params = new URLSearchParams(location.search);
  const auto = params.get("replay") ? { source: "replay", recording: params.get("replay") } : params.get("fixture") ? { source: "fixture", fixture: params.get("fixture") } : null;
  if (auto) setTimeout(() => { follow(); api("/api/incidents", auto).catch((e) => toast(e.message, "fail")); }, 600);

  loadFixtures().catch(() => {});
  refreshRuns();
  connect();
})();
