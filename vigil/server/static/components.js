/* Renderers for the station. The server sends typed components; these turn them into calm, readable views. */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (v) => (typeof v !== "number" ? esc(v) : Number.isInteger(v) ? String(v) : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
  const pretty = (s) => esc(String(s ?? "").replace(/_/g, " "));
  const cite = (ids) => (ids || []).filter(Boolean).map((id) => `<button class="cite" data-cite="${esc(id)}">${esc(id)}</button>`).join("");
  const AT_RISK = /pinned|trapped|injured|unresponsive|coughing|exposed|collapsed/i;
  const METRIC = { emf_milligauss: "EMF", emf: "EMF", temp_c: "Temp", temperature: "Temp", co_ppm: "CO", smoke_ppm: "Smoke", motion_events: "Motion", occupancy: "People", power_v: "Power", network_up: "Network", lux: "Light" };
  const metricName = (m) => METRIC[m] || String(m).replace(/_/g, " ");
  const firstSentence = (t) => { const m = String(t || "").match(/^.*?[.!?](\s|$)/); return (m ? m[0] : String(t || "")).trim(); };

  /* ------------------------------------------------------------------ floor plan */
  function mapSVG(fac, d) {
    if (!fac || !fac.sectors) return `<div class="map-empty">No floor plan for this facility.</div>`;
    const hot = new Set(d.hot || []), iso = new Set(d.iso || []), safe = new Set(d.safe || []);
    const C = {};
    fac.sectors.forEach((s) => (C[s.id] = [s.x + s.w / 2, s.y + s.h / 2]));
    const rooms = fac.sectors.map((s) => {
      const cls = s.is_exit ? "exit" : hot.has(s.id) ? "hot" : iso.has(s.id) ? "iso" : safe.has(s.id) ? "safe" : "";
      const x = s.x + 5, y = s.y + 5, w = s.w - 10, h = s.h - 10;
      const chips = (d.readings[s.id] || []).slice(0, 1).map((r, i) => {
        const text = `${metricName(r.metric)} ${fmt(r.value)}${r.unit && r.unit !== "state" ? " " + r.unit : ""}`;
        const cw = Math.min(w - 28, text.length * 9.4 + 20);
        return `<g class="chip ${esc(r.status)}"><rect x="${x + 14}" y="${y + 46 + i * 30}" width="${cw}" height="24" rx="12"/><text x="${x + 24}" y="${y + 63 + i * 30}">${esc(text)}</text></g>`;
      }).join("");
      const ppl = d.people[s.id];
      const badge = ppl && ppl.count ? `<g class="ppl ${ppl.risk ? "risk" : ""}"><circle cx="${x + w - 26}" cy="${y + h - 26}" r="19"/><text x="${x + w - 26}" y="${y + h - 20}">${ppl.count}</text></g>` : "";
      const pulse = "";
      const label = s.label.length * 10 > w - 24 ? s.label.split(" ")[0] : s.label;
      return `<g class="room ${cls}">${pulse}<rect class="box" x="${x}" y="${y}" width="${w}" height="${h}" rx="16"/><text class="lbl" x="${x + 16}" y="${y + 30}">${esc(label)}</text>${s.is_exit ? `<text class="tag" x="${x + 16}" y="${y + 52}">EXIT</text>` : ""}${chips}${badge}</g>`;
    }).join("");
    const routes = (d.routes || []).map((r, i) => {
      const seq = [r.from_sector, ...(r.via || []), r.to].filter((id, j, a) => id && C[id] && a.indexOf(id) === j);
      if (seq.length < 2) return "";
      const pts = seq.map((id) => C[id]);
      let path = `M${pts[0][0]},${pts[0][1]}`;
      for (let k = 1; k < pts.length; k++) path += ` L${pts[k][0]},${pts[k][1]}`;
      return `<path id="rt${i}" class="route" d="${path}" marker-end="url(#arr)"/><circle class="runner" r="7"><animateMotion dur="${2 + seq.length * 0.6}s" repeatCount="indefinite" rotate="auto"><mpath href="#rt${i}"/></animateMotion></circle>`;
    }).join("");
    return `<svg class="plan" viewBox="0 0 ${fac.width} ${fac.height}" preserveAspectRatio="xMidYMid meet">
      <defs>
        <pattern id="hatch" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="12" height="12" class="hatch-bg"/><line x1="0" y1="0" x2="0" y2="12" class="hatch-ln"/></pattern>
        <marker id="arr" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" class="arr"/></marker>
      </defs>${rooms}${routes}</svg>`;
  }

  /* ------------------------------------------------------------------ tabs */
  function draft(c) { return c && c.provisional ? `<span class="draft">draft</span>` : ""; }

  function renderDo(T) {
    const rb = (T.Runbook || [])[0], mon = (T.MonitorPanel || [])[0];
    if (!rb && !mon) return empty("The commander writes the next ten minutes once the specialists report.");
    let html = "";
    if (mon) html += `<div class="note-card">${esc(mon.props.message)}<div class="muted">Next check: ${esc(mon.props.next_check)}</div></div>`;
    if (rb) html += `<ol class="checklist">${rb.props.steps.map((s, i) => `<li><label><input type="checkbox" data-step="${i}"><span class="ck"></span><span class="txt">${esc(s.text)}</span></label><span class="who">${pretty(s.owner)}</span>${cite([s.source_id])}</li>`).join("")}</ol>`;
    return html;
  }

  function renderOptions(T, selected) {
    const ro = (T.ResponseOptions || [])[0], ex = (T.ExternalContext || [])[0];
    if (!ro && !ex) return empty("The field researcher is gathering guidance. Options appear here with their sources.");
    let html = "";
    if (ro) html += `<div class="opts">${draft(ro)}${ro.props.options.map((o) => `<label class="opt ${o.recommended ? "rec" : ""}"><input type="checkbox" value="${esc(o.id)}" ${selected.includes(o.id) ? "checked" : ""}><span class="ck"></span><div><div class="opt-t">${esc(o.label)}${o.recommended ? `<span class="pill ok">recommended</span>` : ""}<span class="pill ${esc(o.risk)}">${esc(o.risk)} risk</span></div><div class="opt-r">${esc(o.rationale)} ${cite(o.source_ids)}</div></div></label>`).join("")}</div><div class="hint">Tick options to arm them. They are folded into the next update.</div>`;
    if (ex && ex.props.items.length) html += `<h4>Outside the building</h4><div class="ctx">${ex.props.items.map((i) => `<div><span class="pill">${pretty(i.kind)}</span><b>${esc(i.title)}</b> ${esc(i.value)} ${cite([i.source_id])}</div>`).join("")}</div>`;
    return html;
  }

  function spark(points) {
    const vals = points.map((p) => p.v), min = Math.min(...vals), max = Math.max(...vals), W = 120, H = 28;
    const x = (i) => 3 + (i * (W - 6)) / Math.max(1, points.length - 1), y = (v) => (max === min ? H / 2 : H - 3 - ((v - min) * (H - 6)) / (max - min));
    return `<svg class="spark" viewBox="0 0 ${W} ${H}"><path d="${points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ")}"/>${points.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="2.4"/>`).join("")}</svg>`;
  }

  function renderReadings(T) {
    const panels = T.SensorPanel || [], trends = T.TrendTimeline || [], an = (T.AnomalyAssessment || [])[0];
    if (!panels.length && !an) return empty("The sensor analyst is reading the sensors.");
    let html = "";
    if (an) {
      const sc = Math.max(0, Math.min(1, an.props.score)), r = 22, c = 2 * Math.PI * r;
      const col = sc >= 0.7 ? "var(--danger)" : sc >= 0.4 ? "var(--warn)" : "var(--ok)";
      html += `<div class="anomaly">${draft(an)}<svg viewBox="0 0 56 56"><circle cx="28" cy="28" r="${r}" class="track"/><circle cx="28" cy="28" r="${r}" stroke="${col}" stroke-dasharray="${(c * sc).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 28 28)" class="val"/><text x="28" y="33">${Math.round(sc * 100)}</text></svg><div><div class="an-t">Looks like: ${pretty(an.props.matched_signature)}</div><div class="muted">${esc(firstSentence(an.props.rationale))}</div></div></div>`;
    }
    const trendBy = {};
    trends.forEach((t) => ((trendBy[t.props.sector] = trendBy[t.props.sector] || {})[t.props.metric] = t.props));
    html += panels.map((p) => `<div class="sector">${draft(p)}<div class="sector-h">${pretty(p.props.sector)}</div>${p.props.readings.map((r) => {
      const t = (trendBy[p.props.sector] || {})[r.metric];
      return `<div class="reading"><span class="dot ${esc(r.status)}"></span><span class="m">${esc(metricName(r.metric))}</span><span class="v">${fmt(r.value)} <i>${esc(r.unit === "state" ? "" : r.unit)}</i></span>${t && t.points.length > 1 ? spark(t.points) : `<span class="n">${esc(r.note)}</span>`}</div>`;
    }).join("")}</div>`).join("");
    return html;
  }

  function renderPeople(T) {
    const pr = (T.PeopleAtRisk || [])[0], er = (T.EvacuationRoute || [])[0];
    if (!pr && !er) return empty("The facility lead is counting people and computing routes.");
    let html = "";
    if (pr) html += `<div class="groups">${draft(pr)}${pr.props.groups.map((g) => `<div class="group ${AT_RISK.test(g.status) ? "risk" : ""}"><div class="n">${fmt(g.count)}</div><div class="g-s"><b>${pretty(g.sector)}</b><span>${esc(g.status)}</span></div><div class="g-a">${esc(g.action)}</div></div>`).join("")}</div>`;
    if (er) html += `<h4>Routes</h4>${er.props.routes.map((r) => `<div class="route-row"><span class="rp">${pretty(r.from_sector)}</span>${(r.via || []).map((v) => `<span class="sep">›</span><span class="rp">${pretty(v)}</span>`).join("")}<span class="sep">›</span><span class="rp exit">${pretty(r.to)}</span><div class="muted">${esc(r.note)}</div></div>`).join("")}`;
    return html;
  }

  function renderSources(sources) {
    if (!sources.length) return empty("Every citation in the brief points to a real page found during the run. They are listed here.");
    return sources.map((s) => `<div class="src" id="src-${esc(s.id)}"><span class="sid">${esc(s.id)}</span><div><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a><div class="muted">${esc(s.snippet)}</div></div></div>`).join("");
  }

  function renderActivity(items) {
    if (!items.length) return empty("Each agent's steps appear here as they happen.");
    return `<div class="feed">${items.map((a) => `<div class="fi"><span class="av ${esc(a.agent)}">${esc(a.agent[0].toUpperCase())}</span><div><b>${esc(a.name)}</b> ${a.html}</div><span class="t">${a.t != null ? Number(a.t).toFixed(0) + "s" : ""}</span></div>`).join("")}</div>`;
  }

  function empty(text) { return `<div class="empty-tab">${esc(text)}</div>`; }

  window.V = { esc, fmt, pretty, mapSVG, renderDo, renderOptions, renderReadings, renderPeople, renderSources, renderActivity, firstSentence, AT_RISK };
})();
