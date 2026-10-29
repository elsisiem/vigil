/* Component registry: one renderer per catalog entry. The server never sends HTML; it sends
   {component, props} and this file decides how each one looks. */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (v) => {
    if (typeof v !== "number") return esc(v);
    if (Number.isInteger(v)) return String(v);
    return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
  };
  const el = (html) => {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  };
  const pretty = (s) => esc(String(s ?? "").replace(/_/g, " "));

  function card(name, owner, bodyHtml, span) {
    const c = el(`<div class="tile ${span || ""}" data-owner="${esc(owner)}"><div class="tile-head"><h3>${esc(name)}</h3><span class="prov">provisional</span></div><div class="tile-body"></div></div>`);
    c.querySelector(".tile-body").innerHTML = bodyHtml;
    return c;
  }

  function cites(ids) {
    if (!ids || !ids.length) return "";
    return ids.map((id) => `<span class="cite" data-cite="${esc(id)}">[${esc(id)}]</span>`).join("");
  }
  const cite1 = (id) => (id ? cites([id]) : "");

  const TITLES = {
    SensorPanel: "sensors", TrendTimeline: "trend", AnomalyAssessment: "anomaly",
    FacilityMap: "floor plan", PeopleAtRisk: "people at risk", EvacuationRoute: "routes",
    ResponseOptions: "response options", ExternalContext: "outside the building",
    DecisionPanel: "decision", DispatchComposer: "dispatch", Runbook: "runbook · next 10 minutes",
    MonitorPanel: "monitoring",
  };

  const R = {};

  R.IncidentHeader = (p) => {
    const sevColor = p.severity >= 4 ? "var(--danger)" : p.severity === 3 ? "var(--warn)" : p.severity > 0 ? "var(--accent)" : "var(--line)";
    return el(`<div class="headline">
      <div class="meta">${esc(p.incident_id)} · ${esc(p.facility)} · ${esc(p.source)} · ${esc((p.received_at || "").replace("T", " ").slice(0, 16))}</div>
      <h1 class="title">${esc(p.title)}</h1>
      <div class="meta2"><span class="cls">${pretty(p.incident_class)}</span><span class="sev l${p.severity}" style="--sev-color:${sevColor}" title="severity ${p.severity}/5"><i></i><i></i><i></i><i></i><i></i></span></div>
    </div>`);
  };

  R.SeverityBanner = (p) => el(`<div class="banner ${p.is_control ? "control" : "l" + p.level}">
      <span>${esc(p.label)}</span><span style="opacity:.7">severity ${p.level}/5</span>
      <span class="people">${p.people_at_risk ? `${p.people_at_risk} ${p.people_at_risk === 1 ? "person" : "people"} at risk` : "nobody at risk"}</span>
    </div>`);

  R.SensorPanel = (p) => {
    const rows = (p.readings || []).map((r) => `<tr><td><span class="status-dot ${esc(r.status)}"></span>${pretty(r.metric)}</td><td class="val">${fmt(r.value)} ${esc(r.unit)}</td><td class="note">${esc(r.note)}</td></tr>`).join("");
    return card(`${TITLES.SensorPanel} · ${pretty(p.sector)}`, "sensors", `<table class="readings">${rows}</table>`);
  };

  R.TrendTimeline = (p) => {
    const pts = p.points || [];
    const vals = pts.map((x) => x.v);
    const min = Math.min(...vals), max = Math.max(...vals);
    const W = 300, H = 64, pad = 6;
    const x = (i) => pad + (i * (W - 2 * pad)) / Math.max(1, pts.length - 1);
    const y = (v) => (max === min ? (H - 12) / 2 : H - 14 - ((v - min) * (H - 14 - pad)) / (max - min));
    const d = pts.map((pt, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(pt.v).toFixed(1)}`).join(" ");
    const dots = pts.map((pt, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(pt.v).toFixed(1)}" r="3"></circle>`).join("");
    const labels = pts.map((pt, i) => `<text x="${x(i).toFixed(1)}" y="${H - 1}" font-size="9" text-anchor="${i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"}">${esc(pt.t)}</text>`).join("");
    const arrow = p.direction === "rising" ? "↑" : p.direction === "falling" ? "↓" : "→";
    const body = `<div class="note" style="margin-bottom:4px">${esc(p.unit)} · ${esc(p.direction)} ${arrow} · ${fmt(vals[0])} → ${fmt(vals[vals.length - 1])}</div>
      <svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke-width="1.5"></path>${dots}${labels}</svg>
      <div class="note">${esc(p.note)}</div>`;
    return card(`${TITLES.TrendTimeline} · ${pretty(p.metric)} · ${pretty(p.sector)}`, "sensors", body);
  };

  R.AnomalyAssessment = (p) => {
    const score = Math.max(0, Math.min(1, p.score || 0));
    const r = 30, c = 2 * Math.PI * r;
    const color = score >= 0.7 ? "var(--danger)" : score >= 0.4 ? "var(--warn)" : "var(--ok)";
    const body = `<div class="ring">
      <svg viewBox="0 0 72 72"><circle class="track" cx="36" cy="36" r="${r}" fill="none" stroke-width="7"></circle>
        <circle cx="36" cy="36" r="${r}" fill="none" stroke="${color}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${(c * score).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 36 36)"></circle>
        <text x="36" y="41" text-anchor="middle" font-size="15" font-weight="500">${(score * 100).toFixed(0)}</text></svg>
      <div><div class="big">${pretty(p.matched_signature)}</div><div class="note">closest hazard signature · match ${(p.confidence * 100).toFixed(0)}%</div></div></div>
      <div class="note" style="margin-top:8px">${esc(p.rationale)}</div>`;
    return card(TITLES.AnomalyAssessment, "sensors", body);
  };

  function wrapLabel(label, maxChars) {
    const words = String(label).split(" ");
    const lines = [];
    let cur = "";
    for (const w of words) {
      if ((cur + " " + w).trim().length > maxChars && cur) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim();
    }
    if (cur) lines.push(cur);
    return lines.slice(0, 3);
  }

  function floorplan(facility, p, routes) {
    if (!facility) return `<div class="muted">No floor plan for this facility.</div>`;
    const hl = new Set(p.highlight_sectors || []), iso = new Set(p.isolated_sectors || []), safe = new Set(p.safe_sectors || []);
    const centers = {};
    const sectors = facility.sectors.map((s) => {
      centers[s.id] = [s.x + s.w / 2, s.y + s.h / 2];
      const cls = ["sector", hl.has(s.id) ? "highlight" : "", iso.has(s.id) && !hl.has(s.id) ? "isolated" : "", safe.has(s.id) && !hl.has(s.id) && !iso.has(s.id) ? "safe" : "", s.is_exit ? "exit" : ""].join(" ");
      const lines = wrapLabel(s.label, Math.max(6, Math.floor(s.w / 13)));
      const text = lines.map((ln, i) => `<text x="${s.x + s.w / 2}" y="${s.y + s.h / 2 + (i - (lines.length - 1) / 2) * 24}" text-anchor="middle" dominant-baseline="middle">${esc(ln)}</text>`).join("");
      return `<g class="${cls}"><rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="10"></rect>${text}</g>`;
    }).join("");
    const paths = (routes || []).map((r) => {
      const seq = [r.from_sector, ...(r.via || []), r.to].filter((id, i, a) => id && a.indexOf(id) === i && centers[id]);
      if (seq.length < 2) return "";
      const d = seq.map((id, i) => `${i ? "L" : "M"}${centers[id][0]},${centers[id][1]}`).join(" ");
      return `<path class="route" d="${d}" marker-end="url(#arrow)"></path>`;
    }).join("");
    return `<svg class="floorplan" viewBox="0 0 ${facility.width} ${facility.height}">
      <defs>
        <pattern id="hatch" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="14" height="14" fill="#f1f1f3"></rect><line x1="0" y1="0" x2="0" y2="14" stroke="rgba(178,107,0,.45)" stroke-width="4"></line></pattern>
        <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--ok)"></path></marker>
      </defs>${sectors}${paths}</svg>
      <div class="legend"><span><i style="background:var(--danger)"></i>affected</span><span><i style="background:var(--warn)"></i>isolate</span><span><i style="background:var(--ok)"></i>safe</span><span><i style="background:var(--accent)"></i>exit</span>${routes && routes.length ? '<span><i style="background:var(--ok)"></i>route</span>' : ""}</div>
      <div class="note" style="margin-top:6px">${esc(p.caption)}</div>`;
  }

  R.FacilityMap = (p, ctx) => card(`${esc(ctx.facility ? ctx.facility.name : "floor plan")} · ${esc(ctx.facility ? ctx.facility.floor : "")}`, "facility", floorplan(ctx.facility, p, ctx.routes), "map");

  R.PeopleAtRisk = (p) => {
    const groups = (p.groups || []).map((g) => `<div class="group"><div class="n">${fmt(g.count)}</div><div><b>${pretty(g.sector)}</b></div><div class="s">${esc(g.status)}</div><div class="a">${esc(g.action)}</div></div>`).join("");
    return card(`${TITLES.PeopleAtRisk} · ${fmt(p.total_at_risk)}`, "facility", `<div class="groups">${groups}</div>`);
  };

  R.EvacuationRoute = (p) => {
    const routes = (p.routes || []).map((r) => `<div><span class="chip">${pretty(r.from_sector)}</span> → ${(r.via || []).map((v) => `<span class="chip">${pretty(v)}</span> → `).join("")}<span class="chip exit">${pretty(r.to)}</span>${(r.avoid || []).length ? `<span class="muted small"> avoid </span>${r.avoid.map((a) => `<span class="chip avoid">${pretty(a)}</span>`).join("")}` : ""}<div class="note">${esc(r.note)}</div></div>`).join("");
    return card(TITLES.EvacuationRoute, "facility", `<div class="routes">${routes}</div>`);
  };

  R.ResponseOptions = (p, ctx) => {
    const opts = (p.options || []).map((o) => `<label class="opt"><input type="checkbox" value="${esc(o.id)}" ${ctx.selected && ctx.selected.includes(o.id) ? "checked" : ""}><div><div class="l">${esc(o.label)}${o.recommended ? '<span class="badge rec">recommended</span>' : ""}<span class="badge ${esc(o.risk)}">${esc(o.risk)} risk</span></div><div class="r">${esc(o.rationale)} ${cites(o.source_ids)}</div></div></label>`).join("");
    const c = card(TITLES.ResponseOptions, "research", `<div class="options">${opts}</div><div class="note" style="margin-top:8px">armed options are folded into the next recompile</div>`);
    c.addEventListener("change", () => {
      const ids = [...c.querySelectorAll("input:checked")].map((i) => i.value);
      ctx.select && ctx.select(ids);
    });
    return c;
  };

  R.ExternalContext = (p) => {
    const items = (p.items || []).map((i) => `<div><span class="k">${pretty(i.kind)}</span><b>${esc(i.title)}</b> — ${esc(i.value)} ${cite1(i.source_id)}</div>`).join("");
    return card(TITLES.ExternalContext, "research", `<div class="ctx">${items || '<span class="muted">Nothing live to report.</span>'}</div>`);
  };

  R.DecisionPanel = (p) => {
    const alts = (p.alternatives || []).map((a) => `<div><b>${esc(a.action)}</b> — ${esc(a.why_not)}</div>`).join("");
    const body = `<div class="decision-line"><span class="action-chip ${esc(p.action)}">${esc(p.action)}</span><span class="muted">confidence ${(p.confidence * 100).toFixed(0)}%</span></div>
      <div class="confbar"><i style="width:${(p.confidence * 100).toFixed(0)}%"></i></div>
      <div>${esc(p.rationale)}</div>
      <div class="alts" style="margin-top:8px">${alts}</div>`;
    return card(TITLES.DecisionPanel, "commander", body);
  };

  R.DispatchComposer = (p) => card(`${TITLES.DispatchComposer} · ${esc(p.channel)}`, "commander", `<div class="dispatch">${esc(p.message)}</div><div class="note" style="margin-top:6px">${p.requires_approval ? "awaiting approval in the console" : "dispatched automatically"}</div>`);

  R.Runbook = (p) => {
    const steps = (p.steps || []).map((s) => `<label><input type="checkbox"><div>${esc(s.text)}<span class="own">${pretty(s.owner)}</span> ${cite1(s.source_id)}</div></label>`).join("");
    return card(TITLES.Runbook, "commander", `<div class="runbook">${steps}</div>`);
  };

  R.MonitorPanel = (p) => card(TITLES.MonitorPanel, "commander", `<div>${esc(p.message)}</div><div class="note" style="margin-top:6px">Next check: ${esc(p.next_check)}</div>`);

  R.Sources = () => null; // rendered in the right rail by app.js

  function render(comp, ctx) {
    const fn = R[comp.component];
    if (fn) return fn(comp.props || {}, ctx);
    return card(comp.component, comp.owner || "system", `<pre class="json">${esc(JSON.stringify(comp.props, null, 2))}</pre>`);
  }

  window.VigilComponents = { render, floorplan, esc, fmt };
})();
