"use strict";
// Focus tracker dashboard. Plain DOM, no libraries. Every piece of logged text
// (window titles, sites) is untrusted and goes in via textContent only.

const TOKEN = document.querySelector('meta[name="focus-token"]').content;
const CATS = ["Tech/Work", "Learning", "Job search", "Study tools", "Communication", "Entertainment", "Uncategorized", "Private"];
const main = document.getElementById("main");
const dayInput = document.getElementById("day");
const tip = document.getElementById("tip");
let todayIso = null;
let refreshTimer = null;
let replayTimer = null;
let flash = "";  // one-time message shown after the next render

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
const clock = (ts) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const clockS = (ts) => { const d = new Date(ts * 1000); return `${clock(ts)}:${pad(d.getSeconds())}`; };
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
    card("Switches per hour of day", vbars(hours.map((i) => d.hourly_switches[i]), hours.map((i) => `${pad(i)}:00`), "switches"))));
  out.push(card("Deep work sessions", sessionCards(d.cards)));
  if (d.breaks.length) {
    out.push(card("Breaks (back-from-break answers)", h("div", { class: "tags" },
      d.breaks.map((b) => h("span", { text: `${b.label}: ${b.count} (${mins(b.minutes)})` })))));
  }
  const status = h("span", { class: "status", text: flash });
  flash = "";
  const del = (range, label) => h("button", { class: "danger", text: label, onclick: async () => {
    if (!confirm(`Delete everything recorded ${range === "hour" ? "in the last hour" : "today"} (activity and screenshots)? This cannot be undone.`)) return;
    try { const r = await post("/api/delete", { range }); flash = `Deleted (${r.screenshots_removed} screenshots).`; render(); }
    catch (e) { status.textContent = `Could not delete: ${e.message}`; }
  } });
  if (d.day === todayIso) out.push(card("Privacy", h("div", { class: "actions" }, del("hour", "Delete last hour"), del("today", "Delete today"), status)));
  return out;
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
  for (let i = 0; i < 24; i++) grid.append(h("b", { text: i % 3 === 0 ? pad(i) : "" }));
  for (const r of rows) {
    grid.append(h("span", { text: r.day.slice(5) }));
    r.minutes.forEach((m, i) => grid.append(h("i", {
      class: m ? `q${Math.min(5, Math.ceil((m / max) * 5))}` : "",
      tip: `${r.day} ${pad(i)}:00  ${m} min deep work`,
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
    c.visits ? vbars(hours.map((i) => c.by_hour[i]), hours.map((i) => `${pad(i)}:00`), "visits") : null));
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
    h("span", { class: "muted", text: showPreview ? "Saved plans are made every Sunday night, before the screenshot wipe." : `Saved ${new Date(p.created * 1000).toLocaleString()}` }))];
  out.push(h("p", {}));
  if (p.confidence === "low") out.push(h("div", { class: "banner", text: `Low confidence. ${p.confidence_note}` }));
  out.push(h("div", { class: "grid" },
    kpi("Tracked days", String(p.tracked_days), `${p.start_day} to ${p.end_day}`),
    kpi("Active", `${p.active_h} h`),
    kpi("Deep work", `${p.deep_h_per_day} h/day`, p.deep.target_h ? `next week's bar ${p.deep.target_h} h/day` : "baseline weeks"),
    kpi("Best hours for deep work", p.best_hours || "–", "from the last 4 weeks")));
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

async function pageSettings() {
  const d = await api("/api/settings");
  const list = (items) => items.length ? h("div", { class: "tags" }, items.map((x) => h("span", { text: x }))) : h("span", { class: "muted", text: "none" });
  const status = h("span", { class: "status", text: flash });
  flash = "";
  const queue = d.queue.length ? h("table", {},
    h("thead", {}, h("tr", {}, h("th", { text: "App or site" }), h("th", { text: "Example title" }), h("th", { class: "num", text: "7 days" }), h("th", { text: "Category" }))),
    h("tbody", {}, d.queue.map((q) => {
      const sel = h("select", { "aria-label": `Category for ${q.key}` }, h("option", { value: "", text: "Choose…" }), d.categories.map((c) => h("option", { value: c, text: c })));
      const save = h("button", { text: "Save", onclick: async () => {
        if (!sel.value) return;
        try { await post("/api/tag", { kind: q.kind, key: q.key, category: sel.value }); flash = `${q.key} is now ${sel.value}.`; render(); }
        catch (e) { status.textContent = `Could not save: ${e.message}`; }
      } });
      return h("tr", {}, h("td", { text: `${q.key} (${q.kind})` }), h("td", { class: "muted", text: q.example }),
        h("td", { class: "num", text: mins(q.minutes) }), h("td", {}, h("div", { class: "actions" }, sel, save)));
    }))) : empty("Everything you used in the last 7 days has a category.");
  return [
    card("Tagging queue (uncategorised apps and sites)", h("p", { class: "muted", text: `Saving adds a rule to ${d.rules_file} (git-ignored). Productive: ${d.productive.join(", ")}.` }), queue, status),
    card("Current settings (edit config.toml, then restart the tracker)", h("table", {}, h("tbody", {},
      [["Screenshots", `every ${d.screenshot_interval_s} s while active`], ["Idle after", `${d.idle_threshold_min} min`],
       ["Weekly wipe", d.wipe], ["Storage", `${d.storage_gb} GB of ${d.max_storage_gb} GB cap`], ["Day starts", d.day_starts],
       ["Pause hotkey", d.pause_hotkey || "off"]].map(([k, v]) => h("tr", {}, h("th", { text: k }), h("td", { text: v })))))),
    card("Never recorded (logged as [excluded])", h("p", { class: "muted", text: "Apps" }), list(d.exclude_processes),
      h("p", { class: "muted", text: "Title keywords" }), list(d.exclude_title_keywords), h("p", { class: "muted", text: "Sites" }), list(d.exclude_domains)),
    card("No screenshots (time still counts)", h("p", { class: "muted", text: "Title keywords" }), list(d.no_screenshot_title_keywords),
      h("p", { class: "muted", text: "Sites" }), list(d.no_screenshot_domains)),
  ];
}

// router -----------------------------------------------------------------------------
const PAGES = { today: pageToday, focus: pageFocus, distractions: pageDistractions, replay: pageReplay, plan: pagePlan, settings: pageSettings };
async function render() {
  const page = (location.hash.slice(1) || "today").split("?")[0];
  const fn = PAGES[page] || pageToday;
  for (const a of document.querySelectorAll("nav a")) a.classList.toggle("on", a.getAttribute("href") === `#${page}`);
  if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
  clearTimeout(refreshTimer);
  try {
    const nodes = await fn();
    main.replaceChildren(...nodes);
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
window.addEventListener("hashchange", render);
render();
