"use strict";
// Focus tracker dashboard. Plain DOM, no libraries. Every piece of logged text
// (window titles, sites) is untrusted and goes in via textContent only.

const TOKEN = document.querySelector('meta[name="focus-token"]').content;
const CATS = ["Tech/Work", "Learning", "Job search", "Study tools", "Communication", "Entertainment", "System", "Other", "Private"];
const main = document.getElementById("main");
const dayInput = document.getElementById("day");
const tip = document.getElementById("tip");
let todayIso = null;
let refreshTimer = null;
let replayTimer = null;

// helpers --------------------------------------------------------------------
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "style") Object.assign(el.style, v);
    else if (k === "tip") el.dataset.tip = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const slug = (s) => "c-" + String(s).replace(/[^a-z]/gi, "");
const pad = (n) => String(n).padStart(2, "0");
// Every time shown is 12-hour with AM/PM (9:05 AM); stored data stays epoch seconds.
const ampm = (hr) => (hr % 24 < 12 ? "AM" : "PM");
const hour12 = (hr) => `${(hr % 12) || 12} ${ampm(hr)}`;
const clock = (ts) => { const d = new Date(ts * 1000); return `${(d.getHours() % 12) || 12}:${pad(d.getMinutes())} ${ampm(d.getHours())}`; };
const clockS = (ts) => { const d = new Date(ts * 1000); return `${(d.getHours() % 12) || 12}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ${ampm(d.getHours())}`; };
// Plans saved before the 12-hour switch hold "09:00 to 11:00": show them as "9 AM to 11 AM".
const hours12Text = (s) => String(s).replace(/\b(\d{2}):00\b/g, (_m, hr) => hour12(Number(hr)));
function mins(m) {
  if (m == null) return "–";
  m = Math.round(m);
  return m >= 60 ? `${Math.floor(m / 60)}h ${pad(m % 60)}m` : `${m}m`;
}
async function api(path) {
  const day = dayInput.value ? `?day=${dayInput.value}` : "";
  const res = await fetch(path + day);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  const data = await res.json();
  todayIso = data.today;
  if (!dayInput.value) dayInput.value = data.today;
  return data;
}
async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Focus-Token": TOKEN },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const card = (title, ...kids) => h("section", { class: "card" }, title ? h("h2", { text: title }) : null, ...kids);
const empty = (text) => h("div", { class: "empty", text });
const kpi = (label, value, sub) =>
  h("div", { class: "card kpi" }, h("div", { class: "l", text: label }), h("div", { class: "v", text: value }),
    sub ? h("div", { class: "s", text: sub }) : null);
function legend(items) {
  return h("div", { class: "legend" }, items.map((c) => h("span", {}, h("i", { class: slug(c) }), c)));
}

// icons (stroke paths, 24x24) used by the dialog and toasts
const SVGNS = "http://www.w3.org/2000/svg";
const ICONS = {
  ok: "M20 6 9 17l-5-5",
  warn: "M12 3 2 20h20zM12 10v4M12 17h.01",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
};
function icon(name) {  // a name from ICONS, or a raw path
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVGNS, "path");
  path.setAttribute("d", ICONS[name] || name);
  svg.append(path);
  return svg;
}

// In-app confirmation dialog (instead of the browser's built-in pop-ups). Resolves true or false.
// `body` is a list of lines (strings) and nodes; text always goes in via textContent.
const dialog = document.getElementById("dialog");
function ask({ title, body = [], ok = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    let answered = false;
    const done = (value) => { if (answered) return; answered = true; dialog.close(); resolve(value); };
    const cancel = h("button", { type: "button", text: "Cancel", onclick: () => done(false) });
    const yes = h("button", { type: "button", class: danger ? "primary danger" : "primary", text: ok, onclick: () => done(true) });
    dialog.className = danger ? "danger" : "";
    dialog.replaceChildren(
      h("div", { class: "dlg-body" }, h("h3", { id: "dialog-title" }, icon(danger ? "warn" : "info"), title),
        body.map((b) => (b instanceof Node ? b : h("p", { text: b })))),
      h("div", { class: "dlg-foot" }, cancel, yes));
    dialog.oncancel = (e) => { e.preventDefault(); done(false); };  // Esc
    dialog.onclick = (e) => { if (e.target === dialog) done(false); };  // click on the backdrop
    dialog.showModal();
    (danger ? cancel : yes).focus();  // a stray Enter never deletes anything
  });
}

// Small confirmation that something happened, bottom right, gone after a few seconds.
const toasts = document.getElementById("toasts");
function toast(text, kind = "ok") {
  const el = h("div", { class: `toast ${kind}` }, icon(kind === "error" ? "warn" : "ok"), h("span", { text }),
    h("button", { class: "x", "aria-label": "Dismiss", text: "\u00d7", onclick: () => el.remove() }));
  toasts.append(el);
  while (toasts.children.length > 3) toasts.firstChild.remove();
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 6000);
}
const longDate = (iso) => new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// tooltips: any element with data-tip
document.addEventListener("mousemove", (e) => {
  const el = e.target.closest && e.target.closest("[data-tip]");
  if (!el) { tip.hidden = true; return; }
  tip.textContent = el.dataset.tip;
  tip.hidden = false;
  const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = e.clientY + 16 + tip.offsetHeight > window.innerHeight ? e.clientY - tip.offsetHeight - 10 : e.clientY + 16;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${y}px`;
});

// charts -----------------------------------------------------------------------
function timeRange(spans, bounds) {
  if (!spans.length) return null;
  let lo = Math.min(...spans.map((s) => s.start));
  let hi = Math.max(...spans.map((s) => s.end));
  lo = Math.max(bounds[0], Math.floor(lo / 3600) * 3600);
  hi = Math.min(bounds[1], Math.ceil(hi / 3600) * 3600);
  return hi > lo ? [lo, hi] : null;
}
function ribbon(spans, range, keyFn, tipFn) {
  const [lo, hi] = range;
  const w = hi - lo;
  const bar = h("div", { class: "ribbon", role: "img", "aria-label": "Timeline of the day" });
  for (const s of spans) {
    const a = Math.max(s.start, lo), b = Math.min(s.end, hi);
    if (b <= a) continue;
    bar.append(h("span", {
      class: slug(keyFn(s)),
      style: { left: `${((a - lo) / w) * 100}%`, width: `${Math.max(((b - a) / w) * 100, 0.15)}%` },
      tip: tipFn(s),
    }));
  }
  const axis = h("div", { class: "axis" });
  const step = w > 12 * 3600 ? 7200 : 3600;
  for (let t = lo; t <= hi; t += step) axis.append(h("span", { style: { left: `${((t - lo) / w) * 100}%` }, text: clock(t) }));
  return h("div", {}, bar, axis);
}
function hbars(rows, fmt = (r) => mins(r.minutes), cls = null) {
  if (!rows.length) return empty("Nothing yet.");
  const max = Math.max(...rows.map((r) => r.minutes), 1);
  return h("div", { class: "hbars" }, rows.map((r) => [
    h("span", { class: "n", text: r.label, tip: r.label }),
    h("span", { class: "t" }, h("span", { class: "b " + (cls ? cls(r) : ""), style: { width: `${(r.minutes / max) * 100}%` } })),
    h("span", { class: "x", text: fmt(r) }),
  ]));
}
function vbars(values, labels, unit, every = 2) {
  const max = Math.max(...values, 1);
  return h("div", {},
    h("div", { class: "vbars", role: "img", "aria-label": `Bar chart (${unit})` }, values.map((v, i) =>
      h("div", { tip: `${labels[i]}: ${v} ${unit}` }, h("span", { style: { height: `${(v / max) * 100}%` } })))),
    h("div", { class: "vlabels" }, labels.map((l, i) => h("span", { text: i % every === 0 ? l : "" }))));
}
function hourWindow(values) {
  // Trim to the hours that have data, keep at least 8 hours wide.
  let first = values.findIndex((v) => v > 0), last = values.length - 1 - [...values].reverse().findIndex((v) => v > 0);
  if (first < 0) { first = 8; last = 20; }
  if (last - first < 8) last = Math.min(23, first + 8);
  const hours = [];
  for (let i = first; i <= last; i++) hours.push(i);
  return hours;
}
function sessionCards(cards) {
  if (!cards.length) return empty("No deep blocks yet (25+ min on-goal with no distraction over 60 s).");
  return h("div", { class: "sessions" }, cards.map((c) => h("div", { class: "session" },
    h("div", {}, h("strong", { text: mins(c.minutes) }), h("span", { class: "when", text: `  ${clock(c.start)} to ${clock(c.end)}` })),
    h("ul", {}, c.topics.map((t) => h("li", { text: t }))),
    h("div", { class: "muted", text: c.interrupted_by === "still going" ? "Still going" : `Ended by: ${c.interrupted_by}` }))));
}

// pages ----------------------------------------------------------------------------
async function pageToday() {
  const d = await api("/api/today");
  const k = d.kpis;
  const out = [];
  out.push(h("p", { class: "sub" }, d.goal ? ["Goal: ", h("strong", { text: d.goal }),
    d.keywords.length ? h("span", { class: "muted", text: `  (${d.keywords.join(", ")})` }) : null] : "No goal set for this day."));
  if (d.paused_until * 1000 > Date.now()) out.push(h("p", { class: "status", text: `Paused until ${clock(d.paused_until)}.` }));
  out.push(h("div", { class: "grid" },
    kpi("Active", mins(k.active_min), k.watching_min ? `incl. ${mins(k.watching_min)} watching` : null),
    kpi("Deep work", mins(k.deep_min), `${k.deep_blocks} block${k.deep_blocks === 1 ? "" : "s"}, longest ${mins(k.longest_block_min)}`),
    kpi("Switches / hour", String(k.switches_per_hour), `${k.app_switches} app, ${k.tab_switches} tab`),
    kpi("Distraction", mins(k.distraction_min), "off-goal + distraction"),
    kpi("Goal fit", `${k.goal_fit_pct}%`, "of active time on-goal"),
    edgeKpi(d.edges.rows.find((r) => r.day === d.day)),
    kpi(d.day === todayIso ? "Current run" : "First deep block",
      d.day === todayIso ? mins(k.current_run_min) : (k.time_to_first_block_min == null ? "–" : `after ${mins(k.time_to_first_block_min)}`),
      d.day === todayIso ? (k.time_to_first_block_min == null ? "no deep block yet" : `first block after ${mins(k.time_to_first_block_min)}`) : null)));
  const spans = d.ribbon.concat(d.states.map((s) => ({ ...s, category: s.state })));
  const range = timeRange(d.ribbon, d.bounds);
  out.push(card("The day by category", range
    ? [ribbon(spans, range, (s) => s.category, (s) => `${s.category}\n${clock(s.start)} to ${clock(s.end)} (${mins((s.end - s.start) / 60)})`),
       legend(CATS.filter((c) => d.ribbon.some((r) => r.category === c)).concat(d.states.length ? ["idle"] : []))]
    : empty("Nothing recorded for this day.")));
  const hours = hourWindow(d.hourly_switches);
  out.push(h("div", { class: "cols" },
    card("Time by category", hbars(d.categories, undefined, (r) => slug(r.label))),
    card("Switches per hour of day", vbars(hours.map((i) => d.hourly_switches[i]), hours.map((i) => hour12(i)), "switches"))));
  out.push(card("Deep work sessions", sessionCards(d.cards)));
  if (d.breaks.length) {
    out.push(card("Breaks (back-from-break answers)", h("div", { class: "tags" },
      d.breaks.map((b) => h("span", { class: b.rest ? "rest" : null, text: `${b.label}: ${b.count} (${mins(b.minutes)})` }))),
      h("p", { class: "muted", text: `Rest (breaks and naps): ${mins(d.rest_min)}. Time away from the PC never counts as distraction; "Distracted" answers show how often a break was not one.` })));
  }
  out.push(edgesCard(d.edges, d.day));
  return out;
}

// First and last moment at the PC per tracker day ---------------------------------
function edgeText(r) {
  if (!r || r.first == null) return "–";
  return `${clock(r.first)} to ${r.still_on ? "now" : clock(r.last)}`;
}
function edgeKpi(r) {
  const short = r && r.first != null ? `${clock(r.first)}–${r.still_on ? "now" : clock(r.last)}` : "–";
  return kpi("At the PC", short, r && r.first != null ? `first in to ${r.still_on ? "now" : "last out"}, ${mins(r.span_min)}` : "nothing recorded");
}
function edgesCard(e, selected) {
  const rows = e.rows.filter((r) => r.first != null);
  if (!rows.length) return card("Start and stop times", empty("Nothing recorded in the last 14 days."));
  const typical = [];
  if (e.start) typical.push(kpi("Usual first in", e.start.median, `earliest ${e.start.earliest}, latest ${e.start.latest} (${e.start.days} days)`));
  if (e.stop) typical.push(kpi("Usual last out", e.stop.median, `earliest ${e.stop.earliest}, latest ${e.stop.latest} (${e.stop.days} days)`));
  const dayLen = 24 * 3600;
  const axisHours = [];
  for (let i = 0; i <= 24; i += 4) axisHours.push(i);
  const startOf = (iso) => { const t = new Date(iso + "T00:00:00"); t.setHours(e.rollover_hour); return t.getTime() / 1000; };
  const lines = h("div", { class: "edges" }, e.rows.map((r) => {
    const lo = startOf(r.day);
    const bar = h("div", { class: "edge-track" });
    if (r.first != null) {
      bar.append(h("span", {
        class: r.still_on ? "live" : null,
        style: { left: `${((r.first - lo) / dayLen) * 100}%`, width: `${Math.max(((r.last - r.first) / dayLen) * 100, 0.4)}%` },
        tip: `${r.day}\n${edgeText(r)} (${mins(r.span_min)} span, ${mins(r.active_min)} active)`,
      }));
    }
    return [
      h("span", { class: "d" + (r.day === selected ? " on" : ""), text: r.day.slice(5) }),
      bar,
      h("span", { class: "x", text: r.first != null ? `${edgeText(r)} · ${mins(r.active_min)} active` : "not used" }),
    ];
  }));
  const axis = h("div", { class: "edge-axis" }, axisHours.map((i) => h("span", {
    style: { left: `${(i / 24) * 100}%` }, text: hour12(e.rollover_hour + i),
  })));
  return card("Start and stop times (last 14 days)", typical.length ? h("div", { class: "grid" }, typical) : null, lines,
    h("div", { class: "edges" }, h("span", {}), axis, h("span", {})),
    h("p", { class: "muted", text: "First in = first window used after the day starts (unlock or wake); last out = last window used before locking, sleeping or leaving. The day runs from the day-start hour to the same hour next morning. Today's stop is left out of the usual last out until the day ends." }));
}

async function pageFocus() {
  const d = await api("/api/focus");
  const out = [h("div", { class: "grid" },
    kpi("Median focus session", mins(d.median_session_min), "time between context switches"),
    kpi("Context switches / hour", String(d.context_switches_per_hour), "category changes"),
    kpi("Recovery time", mins(d.recovery_median_min), "median gap between deep blocks"),
    kpi("Deep blocks", String(d.blocks.length)))];
  out.push(card("Focus session lengths (minutes)", vbars(d.histogram.map((b) => b.count), d.histogram.map((b) => b.bin), "sessions", 1)));
  const rows = d.heatmap.rows;
  const all = rows.flatMap((r) => r.minutes);
  const max = Math.max(...all, 1);
  const grid = h("div", { class: "heat", role: "img", "aria-label": "Deep work minutes by hour, last 4 weeks" });
  grid.append(h("b", {}));
  for (let i = 0; i < 24; i++) grid.append(h("b", { text: i % 3 === 0 ? hour12(i) : "" }));
  for (const r of rows) {
    grid.append(h("span", { text: r.day.slice(5) }));
    r.minutes.forEach((m, i) => grid.append(h("i", {
      class: m ? `q${Math.min(5, Math.ceil((m / max) * 5))}` : "",
      tip: `${r.day} ${hour12(i)}  ${m} min deep work`,
    })));
  }
  out.push(card("Peak focus hours (deep-work minutes, last 4 weeks)", grid,
    h("p", { class: "muted", text: "Darker = more deep work in that hour. Schedule hard work in the darkest columns." })));
  out.push(card("Session cards", sessionCards(d.blocks)));
  return out;
}

async function pageDistractions() {
  const d = await api("/api/distractions");
  const c = d.communication;
  const out = [h("div", { class: "cols" },
    card("Top distractions", hbars(d.distraction)),
    card("Off-goal (tech or learning, but not today's goal)", hbars(d.off_goal)))];
  out.push(card("Triggers: what you were on just before a distraction",
    d.triggers.length ? h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Before" }), h("th", { text: "Went to" }), h("th", { class: "num", text: "Times" }), h("th", { class: "num", text: "Lost" }))),
      h("tbody", {}, d.triggers.map((t) => h("tr", {}, h("td", { text: t.before }), h("td", { text: t.went_to }),
        h("td", { class: "num", text: t.count }), h("td", { class: "num", text: mins(t.minutes) })))))
      : empty("No distraction over 60 s that followed other work.")));
  const hours = hourWindow(c.by_hour);
  out.push(card("Communication pull",
    h("div", { class: "grid" }, kpi("Visits", String(c.visits)), kpi("Per active hour", String(c.per_hour)), kpi("Median visit", mins(c.median_min))),
    c.visits ? vbars(hours.map((i) => c.by_hour[i]), hours.map((i) => hour12(i)), "visits") : null));
  out.push(card("Rabbit holes (20+ min off-goal right after on-goal work)",
    d.rabbit_holes.length ? h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Start" }), h("th", { text: "From" }), h("th", { text: "Sites" }), h("th", { class: "num", text: "Length" }))),
      h("tbody", {}, d.rabbit_holes.map((r) => h("tr", {}, h("td", { text: clock(r.start) }), h("td", { text: r.from }),
        h("td", { text: r.sites.join(", ") }), h("td", { class: "num", text: mins(r.minutes) })))))
      : empty("None.")));
  return out;
}

async function pageReplay() {
  const d = await api("/api/replay");
  if (!d.frames.length) return [card("Replay", empty("No screenshots for this day (they are wiped every week)."))];
  const times = [...new Set(d.frames.map((f) => f[0]))];
  const monitors = [...new Set(d.frames.map((f) => f[1]))].sort();
  const imgs = {}, caps = {};
  const screens = h("div", { class: "screens" }, monitors.map((m) => {
    imgs[m] = h("img", { alt: `Monitor ${m}` });
    caps[m] = h("figcaption");
    return h("figure", {}, imgs[m], caps[m]);
  }));
  const slider = h("input", { type: "range", min: 0, max: times.length - 1, value: times.length - 1, "aria-label": "Time" });
  const now = h("span", { class: "nowline" });
  const label = h("div", { class: "muted" });
  // Latest frame per monitor at or before each time (unchanged screens were not saved).
  const byTime = new Map();
  const last = {};
  let fi = 0;
  for (const t of times) {
    while (fi < d.frames.length && d.frames[fi][0] <= t) { last[d.frames[fi][1]] = d.frames[fi]; fi++; }
    byTime.set(t, { ...last });
  }
  function show() {
    const t = times[+slider.value];
    const at = byTime.get(t);
    now.textContent = clockS(t);
    let text = "";
    for (const m of monitors) {
      const f = at[m];
      if (f) { if (imgs[m].dataset.id !== String(f[2])) { imgs[m].src = `/shot/${f[2]}.jpg`; imgs[m].dataset.id = f[2]; } caps[m].textContent = `Monitor ${m}, saved ${clockS(f[0])}`; }
      else { imgs[m].removeAttribute("src"); caps[m].textContent = `Monitor ${m}: no frame yet`; }
      if (f && f[0] === t) text = d.labels[f[3]] || text;
    }
    label.textContent = text || " ";
  }
  const play = h("button", { text: "Play" });
  play.addEventListener("click", () => {
    if (replayTimer) { clearInterval(replayTimer); replayTimer = null; play.textContent = "Play"; return; }
    play.textContent = "Pause";
    replayTimer = setInterval(() => {
      if (+slider.value >= times.length - 1) { play.click(); return; }
      slider.value = +slider.value + 1; show();
    }, 250);
  });
  slider.addEventListener("input", show);
  show();
  return [card("Replay", h("p", { class: "muted", text: `${times.length} moments with saved frames. Drag, or use the arrow keys on the slider.` }),
    h("div", { class: "scrub" }, play, now, slider), label, screens)];
}

function targetText(t) {
  const what = t.key ? `${t.key}: ` : "";
  return `${what}${t.op === "<=" ? "at most" : "at least"} ${t.value} ${t.unit}`;
}
let planWeek = "";
async function pagePlan() {
  const d = await (async () => {
    const res = await fetch("/api/plan" + (planWeek ? `?week=${planWeek}` : ""));
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return res.json();
  })();
  const showPreview = planWeek === "preview" || (!planWeek && !d.saved);
  const p = showPreview ? d.preview : d.saved;
  const sel = h("select", { "aria-label": "Week" },
    h("option", { value: "preview", text: "Last 7 days (live preview, not saved)" }),
    d.weeks.map((w) => h("option", { value: w, text: `Week ending ${w}` })));
  sel.value = showPreview ? "preview" : p.end_day;
  sel.addEventListener("change", () => { planWeek = sel.value; render(); });
  const out = [h("div", { class: "actions" }, sel,
    h("span", { class: "muted", text: showPreview ? "Saved plans are made every Sunday night, before the screenshot wipe." : `Saved ${new Date(p.created * 1000).toLocaleString(undefined, { hour12: true })}` }))];
  out.push(h("p", {}));
  if (p.confidence === "low") out.push(h("div", { class: "banner", text: `Low confidence. ${p.confidence_note}` }));
  out.push(h("div", { class: "grid" },
    kpi("Tracked days", String(p.tracked_days), `${p.start_day} to ${p.end_day}`),
    kpi("Active", `${p.active_h} h`),
    kpi("Deep work", `${p.deep_h_per_day} h/day`, p.deep.target_h ? `next week's bar ${p.deep.target_h} h/day` : "baseline weeks"),
    kpi("Best hours for deep work", p.best_hours ? hours12Text(p.best_hours) : "–", "from the last 4 weeks")));
  out.push(h("p", { class: "sub", text: p.deep.note }));
  const actionRows = (list) => h("table", {},
    h("thead", {}, h("tr", {}, h("th", { text: "#" }), h("th", { text: "Finding" }), h("th", { class: "num", text: "Cost / week" }),
      h("th", { text: "Action" }), h("th", { text: "Target next week" }))),
    h("tbody", {}, list.map((a, i) => h("tr", {},
      h("td", { class: "rank", text: String(i + 1) }),
      h("td", { text: a.finding }),
      h("td", { class: "num", text: `${a.cost_h} h`, tip: `Score ${a.score} = ${a.cost_h} h x ${a.consistency} consistency / effort ${a.effort}` }),
      h("td", { text: a.action }),
      h("td", { text: targetText(a.target) })))));
  out.push(card("Top 3 changes for next week, ranked by hours recovered",
    p.actions.length ? actionRows(p.actions) : empty(p.tracked_days ? "No pattern big enough to act on. Keep going." : "No tracked days in this week yet.")));
  if (p.graded.length) {
    out.push(card("Last week's targets", h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Action" }), h("th", { text: "Target" }), h("th", { class: "num", text: "Actual" }), h("th", { text: "Result" }))),
      h("tbody", {}, p.graded.map((g) => h("tr", {}, h("td", { text: g.action }), h("td", { text: targetText(g.target) }),
        h("td", { class: "num", text: `${g.actual} ${g.target.unit}` }), h("td", {}, h("span", { class: `result ${g.result}`, text: g.result }))))))));
  }
  if (p.skipped_checks.length) out.push(h("p", { class: "muted", text: `Missed twice, so swapped for the next best: ${p.skipped_checks.join(", ")}.` }));
  if (p.other_findings.length) out.push(card("Other findings (lower score)", actionRows(p.other_findings)));
  if (!showPreview) {
    const status = h("span", { class: "status", text: p.review_status || "" });
    const body = p.review ? h("div", { class: "review", text: p.review })
      : h("div", { class: "actions" },
        h("button", { text: "Write AI review", onclick: async () => {
          try { await post("/api/plan/review", { week: p.end_day }); status.textContent = "Writing… (about a minute)"; setTimeout(render, 20000); }
          catch (e) { status.textContent = `Could not start: ${e.message}`; }
        } }), status);
    out.push(card("AI write-up (optional)", h("p", { class: "muted", text: "Sends only these weekly numbers, redacted, to Claude through your Claude Code login. No screenshots, URLs or logs." }), body));
  }
  return out;
}

// Settings ---------------------------------------------------------------------------
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const THEMES = [
  { id: "auto", name: "Match Windows", bg: "linear-gradient(135deg, #f6f6f4 50%, #0f1724 50%)" },
  { id: "light", name: "Light", bg: "#f6f6f4" },
  { id: "slate", name: "Slate (navy)", bg: "#0f1724", fg: "#172131" },
  { id: "indigo", name: "Indigo", bg: "#14132b", fg: "#1d1b3a" },
  { id: "teal", name: "Deep teal", bg: "#0c1d22", fg: "#12282f" },
  { id: "graphite", name: "Graphite", bg: "#121211", fg: "#1a1a19" },
];
const LISTS = [
  ["exclude_processes", "Never recorded: apps", "Window titles logged as [excluded], no screenshots.", "name.exe"],
  ["exclude_title_keywords", "Never recorded: title keywords", "Any window whose title contains one of these.", "keyword"],
  ["exclude_domains", "Never recorded: sites", "Also covers their subdomains.", "example.com"],
  ["no_screenshot_title_keywords", "No screenshots: title keywords", "Time still counts; the title is kept.", "keyword"],
  ["no_screenshot_domains", "No screenshots: sites", "Time still counts; the title is kept.", "example.com"],
];
const SETTING_NAMES = {
  screenshot_interval_s: "screenshot interval", idle_threshold_s: "idle time", wipe_weekday: "wipe day",
  wipe_time: "wipe time", max_storage_gb: "storage cap", day_rollover_hour: "day start", pause_hotkey: "pause hotkey",
  deep_work_goal_h: "deep-work goal", ask_daily_goal: "daily goal prompt", ask_after_break: "break question",
  ai_review: "AI review",
};

function getTheme() { try { return localStorage.getItem("focus-theme") || "auto"; } catch (_e) { return "auto"; } }
function setTheme(id) {
  try { if (id === "auto") localStorage.removeItem("focus-theme"); else localStorage.setItem("focus-theme", id); } catch (_e) { /* storage blocked */ }
  if (id === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = id;
}

function field(label, control, help) {
  return h("div", { class: "field" }, h("span", { class: "lbl", text: label }), h("div", { class: "row" }, control), help ? h("span", { class: "help", text: help }) : null);
}
function numberInput(value, min, max, step, label) {
  return h("input", { type: "number", value, min, max, step, required: true, "aria-label": label });
}
function select(options, value, label) {
  const el = h("select", { "aria-label": label }, options.map(([v, t]) => h("option", { value: String(v), text: t })));
  el.value = String(value);
  return el;
}

const STORAGE_KEYS = new Set(["wipe_weekday", "wipe_time", "max_storage_gb"]);
function trackingCard(d, part) {  // part: "general" or "storage"; each saves only its own fields
  const v = d.values;
  const interval = numberInput(v.screenshot_interval_s, 1, 600, 1, "Screenshot interval in seconds");
  const idle = numberInput(Math.round(v.idle_threshold_s / 6) / 10, 1, 120, 0.5, "Idle after minutes");
  const dayStart = select([...Array(24).keys()].map((i) => [i, hour12(i)]), v.day_rollover_hour, "Day starts at");
  const goal = numberInput(v.deep_work_goal_h, 0.5, 16, 0.5, "Deep-work goal in hours");
  const hotkey = h("input", { type: "text", value: v.pause_hotkey, placeholder: "ctrl+alt+p", maxlength: 40, "aria-label": "Pause hotkey" });
  const [wh, wm] = v.wipe_time.split(":").map(Number);
  const wipeDay = select(WEEKDAYS.map((n, i) => [i, n]), v.wipe_weekday, "Wipe day");
  const wipeH = select([...Array(12).keys()].map((i) => [i + 1, String(i + 1)]), (wh % 12) || 12, "Wipe hour");
  const wipeM = select([...Array(60).keys()].map((i) => [i, pad(i)]), wm, "Wipe minute");
  const wipeAp = select([["AM", "AM"], ["PM", "PM"]], wh < 12 ? "AM" : "PM", "AM or PM");
  const cap = numberInput(v.max_storage_gb, 1, 2000, 0.5, "Storage cap in GB");
  const checks = [["ask_daily_goal", "Ask for the day's goal at the first unlock"], ["ask_after_break", "Ask what a break was when I come back"],
    ["ai_review", "Write the weekly AI review automatically (sends redacted weekly numbers to Claude)"]]
    .map(([key, text]) => [key, h("input", { type: "checkbox", "aria-label": text, ...(v[key] ? { checked: "" } : {}) }), text]);
  const save = h("button", { class: "primary", text: "Save changes" });

  function edits() {
    const hr = (+wipeH.value % 12) + (wipeAp.value === "PM" ? 12 : 0);
    const next = {
      screenshot_interval_s: Number(interval.value), idle_threshold_s: Math.round(Number(idle.value) * 60),
      day_rollover_hour: Number(dayStart.value), deep_work_goal_h: Number(goal.value), pause_hotkey: hotkey.value.trim().toLowerCase(),
      wipe_weekday: Number(wipeDay.value), wipe_time: `${pad(hr)}:${pad(+wipeM.value)}`, max_storage_gb: Number(cap.value),
    };
    for (const [key, box] of checks) next[key] = box.checked;
    const out = {};
    for (const [k, val] of Object.entries(next)) if (val !== v[k] && STORAGE_KEYS.has(k) === (part === "storage")) out[k] = val;
    return out;
  }
  save.addEventListener("click", async () => {
    for (const el of part === "storage" ? [cap] : [interval, idle, goal]) if (!el.reportValidity()) return;
    const changes = edits();
    if (!Object.keys(changes).length) { toast("Nothing changed."); return; }
    const warnings = [];
    if ("max_storage_gb" in changes && changes.max_storage_gb < d.storage_gb) warnings.push(`Screenshots use ${d.storage_gb} GB, more than the new ${changes.max_storage_gb} GB cap: the oldest days of screenshots will be deleted within 10 minutes.`);
    if ("day_rollover_hour" in changes) warnings.push(`Days will run from ${hour12(changes.day_rollover_hour)} to ${hour12(changes.day_rollover_hour)}. Past days are re-counted with the new start.`);
    if ("wipe_weekday" in changes || "wipe_time" in changes) warnings.push("The weekly wipe and weekly plan move to the new day and time, starting with the next one. Nothing is wiped now.");
    if (warnings.length && !(await ask({ title: "Save these settings?", body: warnings, ok: "Save" }))) return;
    try {
      const r = await post("/api/settings", changes);
      toast(r.changed.length ? `Saved and applied: ${r.changed.map((k) => SETTING_NAMES[k] || k).join(", ")}.` : "Nothing changed.");
      render();
    } catch (e) { toast(`Could not save: ${e.message}`, "error"); }
  });
  const reset = h("button", { text: "Undo changes", onclick: () => render() });
  const hint = h("span", { class: "hint", text: `saved to ${d.config_file}, applied right away` });
  if (part === "storage") {
    return h("section", { class: "card" }, h("h2", {}, "Screenshots and storage", hint),
      h("div", { class: "form" },
        field("Weekly wipe of all screenshots", [wipeDay, "at", wipeH, wipeM, wipeAp], "The activity log is kept; only pictures are wiped."),
        field("Storage cap", [cap, h("span", { class: "muted", text: `GB (now using ${d.storage_gb} GB)` })], "Oldest screenshot days are deleted first if the cap is reached.")),
      h("div", { class: "actions" }, save, reset));
  }
  return h("section", { class: "card" }, h("h2", {}, "Tracking", hint),
    h("div", { class: "group-title", text: "Recording" }),
    h("div", { class: "form" },
      field("Screenshot every", [interval, h("span", { class: "muted", text: "seconds" })], "One picture per monitor while you are active or watching."),
      field("Idle after", [idle, h("span", { class: "muted", text: "minutes" })], "No mouse or keyboard input for this long = idle (no screenshots). Sound playing counts as watching."),
      field("Pause hotkey", hotkey, "Press once to pause for 15 minutes, press again to resume. Modifiers + one letter or digit, e.g. ctrl+alt+p. Empty turns it off.")),
    h("div", { class: "group-title", text: "Days and goals" }),
    h("div", { class: "form" },
      field("Day starts at", dayStart, "Work after midnight still counts for the day before."),
      field("Deep-work goal", [goal, h("span", { class: "muted", text: "hours a day" })], "The weekly plan ramps toward this."),
      h("div", { class: "field" }, checks.map(([, box, text]) => h("label", { class: "check" }, box, h("span", { text }))))),
    h("div", { class: "actions" }, save, reset));
}

function themeCard() {
  const current = getTheme();
  return h("section", { class: "card" }, h("h2", {}, "Appearance", h("span", { class: "hint", text: "saved in this browser" })),
    h("div", { class: "themes" }, THEMES.map((t) => h("button", {
      class: "swatch" + (t.id === current ? " on" : ""), "aria-pressed": String(t.id === current),
      onclick: () => { setTheme(t.id); render(); },
    }, h("i", { style: { background: t.fg ? `linear-gradient(180deg, ${t.fg} 0 30%, ${t.bg} 30%)` : t.bg } }), h("span", { text: t.name })))));
}

function listsCard(d) {
  const state = {};
  const editors = LISTS.map(([key, title, help, placeholder]) => {
    state[key] = [...d.values[key]];
    const locked = new Set(d.built_in[key]);
    const original = new Set(d.values[key]);
    const chips = h("div", { class: "tags" });
    const draw = () => chips.replaceChildren(...(state[key].length ? state[key].map((item) => locked.has(item)
      ? h("span", { class: "chip locked", tip: "Built-in protection: cannot be removed here" }, icon("lock"), item)
      : h("span", { class: "chip" + (original.has(item) ? "" : " new") }, item,
          h("button", { type: "button", "aria-label": `Remove ${item}`, text: "×", onclick: () => { state[key] = state[key].filter((x) => x !== item); draw(); } })))
      : [h("span", { class: "muted", text: "none" })]));
    const input = h("input", { type: "text", placeholder, maxlength: 100, "aria-label": `Add to ${title}` });
    const add = () => {
      const val = input.value.trim().toLowerCase();
      if (!val) return;
      if (!state[key].includes(val)) state[key].push(val);
      input.value = "";
      draw();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    draw();
    return h("div", { class: "listed" }, h("div", { class: "lbl", text: title }), h("div", { class: "muted", text: help }), chips,
      h("div", { class: "actions" }, input, h("button", { type: "button", text: "Add", onclick: add })));
  });
  const save = h("button", { class: "primary", text: "Save lists", onclick: async () => {
    const changes = {}, removed = [];
    for (const [key] of LISTS) {
      if (JSON.stringify(state[key]) !== JSON.stringify(d.values[key])) changes[key] = state[key];
      removed.push(...d.values[key].filter((x) => !state[key].includes(x)));
    }
    if (!Object.keys(changes).length) { toast("Nothing changed."); return; }
    if (removed.length && !(await ask({
      title: "Remove these protections?", danger: true, ok: "Remove and save",
      body: ["These will be recorded (and screenshotted, unless another rule still blocks them) from now on:", h("ul", {}, removed.map((x) => h("li", { text: x })))],
    }))) return;
    try {
      await post("/api/settings", changes);
      toast("Privacy lists saved and applied.");
      render();
    } catch (e) { toast(`Could not save: ${e.message}`, "error"); }
  } });
  return h("section", { class: "card" },
    h("h2", {}, "Privacy lists", h("span", { class: "hint", text: "built-in entries (lock icon) stay; add your own" })),
    editors, h("div", { class: "actions" }, save, h("button", { text: "Undo changes", onclick: () => render() })));
}

function queueCard(d) {
  const queue = d.queue.length ? h("table", {},
    h("thead", {}, h("tr", {}, h("th", { text: "Site or app" }), h("th", { text: "Page or window" }), h("th", { class: "num", text: "7 days" }), h("th", { text: "Category" }))),
    h("tbody", {}, d.queue.map((q) => {
      const sel = h("select", { "aria-label": `Category for ${q.key}` }, h("option", { value: "", text: "Choose…" }), d.categories.map((c) => h("option", { value: c, text: c })));
      const all = h("input", { type: "checkbox", "aria-label": `Use for all of ${q.key}` });
      const save = h("button", { text: "Save", onclick: async () => {
        if (!sel.value) { toast("Pick a category first.", "error"); return; }
        const scope = all.checked ? "all" : "page";
        try {
          await post("/api/tag", { kind: q.kind, key: q.key, title: q.title, scope, category: sel.value });
          toast(scope === "all" ? `All of ${q.key} is now ${sel.value}.` : `That page is now ${sel.value}.`);
          render();
        } catch (e) { toast(`Could not save: ${e.message}`, "error"); }
      } });
      return h("tr", {}, h("td", { text: `${q.key} (${q.kind})` }), h("td", { class: "muted", text: q.example }),
        h("td", { class: "num", text: mins(q.minutes) }),
        h("td", {}, h("div", { class: "actions" }, sel, h("label", { class: "muted" }, all, ` all of ${q.key}`), save)));
    }))) : empty("Everything in the last 7 days was sorted by its screen text or a rule.");
  return h("section", { class: "card" }, h("h2", { text: "Not sure (Other): check these" }),
    h("p", { class: "muted", text: `Most time is sorted from the screen text. These pages could not be: pick a category for that page, or tick the box for the whole site or app. Saved to ${d.rules_file} (git-ignored); your choice beats the screen text. Productive: ${d.productive.join(", ")}. Neutral (neither work nor distraction): ${d.neutral.join(", ")}.` }), queue);
}

// Delete recorded data: two separate boxes, one date picker (for the whole-day delete only).
function deleteCard(today) {
  const delHour = h("button", { class: "solid-danger", text: "Delete the last hour", onclick: async () => {
    if (!(await ask({ title: "Delete the last hour?", danger: true, ok: "Delete last hour",
      body: [`Everything recorded since ${clock(Date.now() / 1000 - 3600)}: activity, switches and screenshots.`, "This cannot be undone."] }))) return;
    try {
      const r = await post("/api/delete", { range: "hour" });
      toast(`Deleted the last hour: all activity and ${plural(r.screenshots_removed, "screenshot")}.`);
      render();
    } catch (e) { toast(`Could not delete: ${e.message}`, "error"); }
  } });

  const picker = h("input", { type: "date", value: today, max: today, "aria-label": "Day to delete" });
  const preview = h("div", { class: "preview" });
  const delDay = h("button", { class: "solid-danger", text: "Delete this day" });
  let summary = null, seq = 0;
  async function load() {
    const day = picker.value, mine = ++seq;
    summary = null;
    delDay.disabled = true;
    delDay.textContent = "Delete this day";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) { preview.textContent = "Pick a day."; return; }
    if (day > today) { preview.textContent = "That day has not happened yet."; return; }
    preview.textContent = "Checking…";
    try {
      const res = await fetch(`/api/day_summary?day=${encodeURIComponent(day)}`);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const s = await res.json();
      if (mine !== seq) return;  // the picker changed meanwhile
      if (s.empty) { preview.textContent = `Nothing is recorded for ${longDate(day)}.`; return; }
      summary = s;
      const bits = [`${mins(s.activity_min)} of activity`, plural(s.screenshots, "screenshot")];
      if (s.goal) bits.push("the goal");
      preview.textContent = `${longDate(day)}, ${clock(s.bounds[0])} to ${clock(s.bounds[1])} next day: ${bits.join(", ")}.`;
      delDay.textContent = `Delete ${longDate(day)}`;
      delDay.disabled = false;
    } catch (e) { if (mine === seq) preview.textContent = `Could not check that day: ${e.message}`; }
  }
  picker.addEventListener("change", load);
  delDay.addEventListener("click", async () => {
    const s = summary;
    if (!s) return;
    const day = s.day;
    const body = [`${longDate(day)} runs from ${clock(s.bounds[0])} to ${clock(s.bounds[1])} the next morning. This removes:`,
      h("ul", {}, [`${mins(s.activity_min)} of activity and switches`, plural(s.screenshots, "screenshot"),
        s.goal ? "the day's goal" : null, "idle and lock times, and break answers"].filter(Boolean).map((t) => h("li", { text: t })))];
    if (s.plans.length) body.push(`The saved weekly plan${s.plans.length === 1 ? "" : "s"} for the week ending ${s.plans.join(", ")} will be rebuilt without this day (any AI review is cleared).`);
    if (day === today) body.push("The tracker keeps running and records again from now on.");
    body.push("This cannot be undone.");
    if (!(await ask({ title: `Delete all data for ${longDate(day)}?`, body, danger: true, ok: "Delete this day" }))) return;
    try {
      const r = await post("/api/delete", { range: "day", day });
      const rebuilt = r.plans_rebuilt.length ? ` Rebuilt ${plural(r.plans_rebuilt.length, "weekly plan")}.` : "";
      toast(`Deleted all data for ${longDate(day)}: ${mins(s.activity_min)} of activity and ${plural(r.screenshots_removed, "screenshot")}.${rebuilt}`);
      render();
    } catch (e) { toast(`Could not delete: ${e.message}`, "error"); }
  });
  load();
  return h("section", { class: "card danger-zone" }, h("h2", { text: "Delete recorded data" }),
    h("p", { class: "muted", text: "Both delete activity, switches, screenshots and break answers. Deleted data cannot be recovered." }),
    h("div", { class: "cols" },
      h("div", { class: "danger-box" }, h("h3", { text: "Last hour" }),
        h("p", { class: "muted", text: "Something private was on screen a moment ago? Remove the last 60 minutes." }), h("div", {}, delHour)),
      h("div", { class: "danger-box" }, h("h3", { text: "A whole day" }),
        h("p", { class: "muted", text: "A day runs from the day-start hour to the same hour the next morning, and includes its goal." }),
        h("label", { class: "field" }, h("span", { class: "lbl", text: "Day to delete" }), picker), preview, h("div", {}, delDay))));
}

const SETTINGS_SECTIONS = [
  { id: "tracking", name: "Tracking", about: "Screenshots, idle, hotkey, goals", icon: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2", card: (d) => trackingCard(d, "general") },
  { id: "storage", name: "Screenshots", about: "Weekly wipe and storage cap", icon: "M3 7h4l2-3h6l2 3h4v13H3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", card: (d) => trackingCard(d, "storage") },
  { id: "privacy", name: "Privacy lists", about: "What is never recorded", icon: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z", card: (d) => listsCard(d) },
  { id: "sort", name: "Not sure", about: "Pick categories for unsorted pages", icon: "M4 6h16M7 12h10M10 18h4", card: (d) => queueCard(d), badge: (d) => d.queue.length },
  { id: "appearance", name: "Appearance", about: "Colour theme", icon: "M12 22a10 10 0 1 1 0-20c5.5 0 10 4 10 8.5 0 3-2.5 4.5-5 4.5h-2a2 2 0 0 0-1.5 3.3A2 2 0 0 1 12 22zM7.5 11h.01M12 7.5h.01M16.5 11h.01", card: () => themeCard() },
  { id: "delete", name: "Delete data", about: "Last hour or a whole day", icon: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3", card: (d) => deleteCard(d.today), danger: true },
];
// Settings: a menu of sub-sections; only the chosen one is shown (#settings/<section>).
async function pageSettings(sub) {
  const d = await api("/api/settings");
  const current = SETTINGS_SECTIONS.find((x) => x.id === sub) || SETTINGS_SECTIONS[0];
  const menu = h("nav", { class: "subnav", "aria-label": "Settings sections" }, SETTINGS_SECTIONS.map((x) => {
    const badge = x.badge ? x.badge(d) : 0;
    return h("a", { href: `#settings/${x.id}`, class: (x === current ? "on" : "") + (x.danger ? " danger" : ""), ...(x === current ? { "aria-current": "page" } : {}) },
      icon(x.icon), h("span", { class: "t" }, h("b", {}, x.name, badge ? h("span", { class: "badge", text: String(badge) }) : null), h("small", { text: x.about })));
  }));
  return [h("div", { class: "settings" }, menu, h("div", { class: "panel" }, current.card(d)))];
}

// router -----------------------------------------------------------------------------
const PAGES = { today: pageToday, focus: pageFocus, distractions: pageDistractions, replay: pageReplay, plan: pagePlan, settings: pageSettings };
const TITLES = { today: "Today", focus: "Focus", distractions: "Distractions", replay: "Replay", plan: "Weekly plan", settings: "Settings" };
const DAY_PAGES = new Set(["today", "focus", "distractions", "replay"]);  // pages that use the day picker
async function render() {
  const [rawPage, sub] = (location.hash.slice(1) || "today").split("?")[0].split("/");
  let page = rawPage;
  if (!PAGES[page]) page = "today";
  for (const a of document.querySelectorAll("nav a")) {
    const on = a.getAttribute("href") === `#${page}`;
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  document.getElementById("title").textContent = TITLES[page];
  document.getElementById("daypick").hidden = !DAY_PAGES.has(page);
  document.title = `${TITLES[page]} · Focus tracker`;
  if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
  clearTimeout(refreshTimer);
  const y = window.scrollY;
  try {
    const nodes = await PAGES[page](sub);
    main.replaceChildren(...nodes);
    if (page === "settings") window.scrollTo(0, y);  // re-render after a save keeps your place
  } catch (e) {
    main.replaceChildren(card("Could not load", empty(e.message)));
  }
  // Keep the live pages fresh while open (not Replay or Settings, which hold your place).
  if ((page === "today" || page === "focus" || page === "distractions") && (!dayInput.value || dayInput.value === todayIso)) {
    refreshTimer = setTimeout(() => { if (!document.hidden) render(); else refreshTimer = setTimeout(render, 60000); }, 60000);
  }
}
function shiftDay(n) {
  const base = dayInput.value ? new Date(dayInput.value + "T12:00:00") : new Date();
  base.setDate(base.getDate() + n);
  dayInput.value = `${base.getFullYear()}-${pad(base.getMonth() + 1)}-${pad(base.getDate())}`;
  render();
}
document.getElementById("prev").addEventListener("click", () => shiftDay(-1));
document.getElementById("next").addEventListener("click", () => shiftDay(1));
dayInput.addEventListener("change", render);
window.addEventListener("hashchange", () => { window.scrollTo(0, 0); render(); });
render();
