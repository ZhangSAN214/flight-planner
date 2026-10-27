"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const state = { currency: "GBP", home: "MAN", lastSearch: null, shown: 20 };

const TAG_LABELS = {
  cheapest: ["最便宜", "good"],
  fastest: ["最快", "good"],
  best: ["综合最优", "good"],
  pareto: ["性价比前沿", ""],
  redeye: ["红眼航班", "warn"],
  tight_connection: ["衔接紧张", "warn"],
  round_trip_total: ["往返总价", ""],
};

async function api(path, opts = {}) {
  const resp = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}

function money(v, cur = state.currency) {
  if (v === null || v === undefined) return "—";
  const sym = { GBP: "£", EUR: "€", USD: "$", CNY: "¥" }[cur] || cur + " ";
  return sym + Math.round(v).toLocaleString();
}
const hm = (m) => `${Math.floor(m / 60)}小时${m % 60 ? (m % 60) + "分" : ""}`;
const hhmm = (iso) => iso.slice(11, 16);
const isoDate = (d) => d.toISOString().slice(0, 10);
const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return isoDate(d); };

function showMsg(id, text, isError = false) {
  $(id).innerHTML = text ? `<div class="msg ${isError ? "error" : ""}">${esc(text)}</div>` : "";
}
function loading(id, text = "查询中…") { $(id).innerHTML = `<div class="loading">${esc(text)}</div>`; }
function formData(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) if (v !== "") out[k] = typeof v === "string" ? v.trim() : v;
  return out;
}
const qs = (obj) => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== "" && v !== null && v !== undefined)).toString();

// ---------- 标签页 ----------
function switchTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  try { localStorage.setItem("tab", name); } catch (e) { /* 忽略 */ }
  if (name === "watches") loadWatches();
  if (name === "deals" && !$("#deals-list").innerHTML) loadDeals();
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

// ---------- 状态 ----------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    state.currency = s.currency;
    state.home = s.home_airport;
    const parts = [];
    if (s.demo_mode) parts.push(`<span class="pill">演示数据</span>在 .env 填写 API key 后显示真实航班`);
    else parts.push(`实时数据：${esc(s.flight_sources.join("、") || "无")}`);
    if (s.calendar_source) parts.push(`缓存价：${esc(s.calendar_source)}`);
    if (!s.demo_mode && s.flight_sources.includes("Google Flights")) parts.push(`本月 SerpApi ${s.serpapi_used_this_month}/${s.serpapi_monthly_limit} 次`);
    parts.push(s.alert_channels.length ? `提醒：${esc(s.alert_channels.join("、"))}` : "提醒：未配置");
    $("#status").innerHTML = parts.join(" · ");
    $$("input[name=origin]").forEach((i) => { if (!i.value) i.value = s.home_airport; });
    if (!$("#deals-form [name=airports]").value) $("#deals-form [name=airports]").value = s.home_airport;
  } catch (e) {
    $("#status").textContent = "无法连接后端：" + e.message;
  }
}

// ---------- 搜索比价 ----------
const searchForm = $("#search-form");
searchForm.hour_value.addEventListener("input", () => { $("#hv-out").textContent = money(+searchForm.hour_value.value); });

searchForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const params = formData(searchForm);
  showMsg("#search-msg", "");
  $("#results").innerHTML = "";
  $("#search-summary").innerHTML = "";
  $("#scatter").classList.add("hidden");
  $("#search-links").classList.add("hidden");
  loading("#search-msg", "正在查询各数据源…");
  try {
    const data = await api("/api/search?" + qs(params));
    state.lastSearch = data;
    state.shown = 20;
    showMsg("#search-msg", data.errors.join("；"));
    renderSearch(data);
  } catch (e) {
    showMsg("#search-msg", e.message, true);
  }
});

function renderSearch(data) {
  const its = data.itineraries;
  renderLinks("#search-links", data.links, "在其他网站核对价格（已填好航线和日期）");
  if (!its.length) {
    $("#results").innerHTML = `<div class="card">没有找到航班。可以试试附近日期或放宽中转限制。</div>`;
    return;
  }
  const pick = (t) => its.find((i) => i.tags.includes(t));
  const card = (k, it) => it ? `<div class="card"><div class="k">${k}</div><div class="v">${money(price(it))}</div><div class="meta">${hm(it.total_minutes)} · 中转 ${it.total_stops} 次 · ${esc(it.airlines.join("/"))}</div></div>` : "";
  $("#search-summary").innerHTML = `<div class="summary">${card("最便宜", pick("cheapest"))}${card("最快", pick("fastest"))}${card("综合最优", pick("best"))}</div>`;
  renderScatter(its);
  renderItineraries();
}

const price = (it) => it.price_display ?? it.price;

function renderItineraries() {
  const its = state.lastSearch.itineraries;
  const html = its.slice(0, state.shown).map(itinCard).join("");
  const more = its.length > state.shown ? `<button class="secondary" id="more">再显示 ${Math.min(20, its.length - state.shown)} 个（共 ${its.length} 个）</button>` : "";
  $("#results").innerHTML = html + more;
  if (more) $("#more").addEventListener("click", () => { state.shown += 20; renderItineraries(); });
  $$("#results [data-booking]").forEach((b) => b.addEventListener("click", () => openBooking(b.dataset.booking)));
  $$("#results [data-watch]").forEach((b) => b.addEventListener("click", () => prefillWatch(b.dataset.watch)));
}

function itinCard(it) {
  const tags = it.tags.map((t) => {
    if (t.startsWith("also:")) return `<span class="tag">${esc(t.slice(5))} 也有</span>`;
    const [label, cls] = TAG_LABELS[t] || [];
    return label ? `<span class="tag ${cls}">${label}</span>` : "";
  }).join("");
  const slices = it.slices.map((s, idx) => {
    const first = s.segments[0], last = s.segments[s.segments.length - 1];
    const dayDiff = Math.round((new Date(last.arrive.slice(0, 10)) - new Date(first.depart.slice(0, 10))) / 86400000);
    const path = [first.origin, ...s.segments.map((g) => g.destination)].join(" → ");
    const label = it.slices.length > 1 ? (idx === 0 ? "去程" : "回程") : "";
    return `<div class="slice">${label ? `<span class="meta">${label}</span>` : ""}
      <span class="time">${first.depart.slice(5, 10)} ${hhmm(first.depart)} – ${hhmm(last.arrive)}${dayDiff > 0 ? `<sup>+${dayDiff}</sup>` : ""}</span>
      <span class="path">${esc(path)}</span>
      <span class="meta">${hm(s.duration_minutes)} · ${s.stops ? `中转 ${s.stops} 次` : "直飞"} · ${esc(s.segments.map((g) => g.flight_number).join(", "))}</span></div>`;
  }).join("");
  const original = it.price_display !== null && it.currency !== state.currency ? `<small>原价 ${money(it.price, it.currency)}</small>` : "";
  return `<div class="card itin">
    <div>${tags}<div class="meta">${esc(it.airlines.join(" / "))} · 来源 ${esc(it.source)}</div>${slices}</div>
    <div class="price">${money(price(it))}${original}<small>综合成本 ${money(it.score)}</small></div>
    <div class="buttons"><button class="secondary" data-booking="${esc(it.id)}">各渠道比价</button>
    <button class="secondary" data-watch="${esc(it.id)}">监控这条航线</button></div></div>`;
}

function renderScatter(its) {
  const W = 700, H = 260, L = 56, B = 34, T = 14, R = 16;
  const xs = its.map((i) => i.total_minutes / 60), ys = its.map(price);
  const x0 = Math.floor(Math.min(...xs)), x1 = Math.ceil(Math.max(...xs)) + 0.5;
  const y0 = Math.min(...ys) * 0.95, y1 = Math.max(...ys) * 1.03;
  const X = (v) => L + (v - x0) / (x1 - x0 || 1) * (W - L - R);
  const Y = (v) => H - B - (v - y0) / (y1 - y0 || 1) * (H - B - T);
  const front = its.filter((i) => i.tags.includes("pareto")).sort((a, b) => a.total_minutes - b.total_minutes);
  const xticks = [], yticks = [];
  const xstep = Math.max(1, Math.ceil((x1 - x0) / 8));
  for (let v = x0; v <= x1; v += xstep) xticks.push(v);
  for (let k = 0; k <= 4; k++) yticks.push(y0 + (y1 - y0) * k / 4);
  const svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="价格与飞行时长">
    ${yticks.map((v) => `<line class="axis" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${money(v)}</text>`).join("")}
    ${xticks.map((v) => `<text x="${X(v)}" y="${H - B + 16}" text-anchor="middle">${v}h</text>`).join("")}
    <text x="${W - R}" y="${H - 4}" text-anchor="end">总飞行时长（小时）</text>
    <polyline class="frontline" points="${front.map((i) => `${X(i.total_minutes / 60)},${Y(price(i))}`).join(" ")}"/>
    ${its.map((i) => `<circle class="dot ${i.tags.includes("pareto") ? "front" : ""}" cx="${X(i.total_minutes / 60)}" cy="${Y(price(i))}" r="5"><title>${esc(i.airlines.join("/"))} ${money(price(i))} ${hm(i.total_minutes)}</title></circle>`).join("")}
  </svg>`;
  const el = $("#scatter");
  el.innerHTML = `<h2>价格 vs 时长</h2><div class="meta">实心点是「性价比前沿」：没有其他方案比它们同时更便宜又更快。虚线越往左下越好。</div>${svg}`;
  el.classList.remove("hidden");
}

function renderLinks(sel, links, title) {
  const el = $(sel);
  el.innerHTML = `<h2>${esc(title)}</h2><div class="links">${links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)}</a>`).join("")}</div>`;
  el.classList.remove("hidden");
}

// ---------- 各渠道比价 ----------
const dialog = $("#booking-dialog");
$("#dialog-close").addEventListener("click", () => dialog.close());

async function openBooking(id) {
  $("#booking-body").innerHTML = `<div class="loading">正在查询各卖家价格…</div>`;
  dialog.showModal();
  try {
    const data = await api("/api/booking-options/" + encodeURIComponent(id));
    const rows = data.options.map((o, idx) => `<tr class="${o.cheapest ? "cheapest" : ""}">
      <td><b>${esc(o.seller)}</b>${o.is_airline ? ` <span class="tag good">航司直销</span>` : ""}${o.cheapest ? ` <span class="tag good">最便宜</span>` : ""}
        <div class="meta">${esc([o.fare_name, ...o.baggage, ...o.conditions].filter(Boolean).join(" · "))}</div></td>
      <td class="num">${money(o.price_display ?? o.price, o.price_display !== null ? state.currency : o.currency)}</td>
      <td>${o.url ? `<button class="secondary" data-go="${idx}">去预订</button>` : ""}</td></tr>`).join("");
    $("#booking-body").innerHTML = (data.options.length
      ? `<table><thead><tr><th>卖家</th><th class="num">价格</th><th></th></tr></thead><tbody>${rows}</tbody></table>
         <p class="hint">价格来自查询时刻，最终以卖家页面为准。比较时注意行李是否包含。</p>`
      : `<p>这个数据源没有提供分卖家报价，可以用下面的链接到各网站核对。</p>`) + `<div id="dialog-links" class="card"></div>`;
    renderLinks("#dialog-links", data.links, "其他网站（含携程国内版、去哪儿、飞猪）");
    $$("#booking-body [data-go]").forEach((b) => b.addEventListener("click", () => goBook(data.options[+b.dataset.go])));
  } catch (e) {
    $("#booking-body").innerHTML = `<div class="msg error">${esc(e.message)}</div>`;
  }
}

function goBook(opt) {
  if (!opt.post_data) { window.open(opt.url, "_blank", "noopener"); return; }
  // Google 的预订跳转需要以 POST 方式提交这段数据
  const form = document.createElement("form");
  form.method = "POST";
  form.action = opt.url;
  form.target = "_blank";
  for (const [k, v] of new URLSearchParams(opt.post_data)) {
    const input = document.createElement("input");
    input.type = "hidden"; input.name = k; input.value = v;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
  form.remove();
}

// ---------- 灵活日期 ----------
$("#flex-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const p = formData(ev.target);
  showMsg("#flex-msg", "");
  loading("#flex-result", "正在读取日历价格…");
  try {
    const data = await api("/api/flexible?" + qs(p));
    showMsg("#flex-msg", data.errors.join("；"));
    renderCalendar(data);
  } catch (e) {
    $("#flex-result").innerHTML = "";
    showMsg("#flex-msg", e.message, true);
  }
});

function heat(t) { return `hsl(${Math.round(130 - 130 * t)} 70% 50% / 0.28)`; }

function renderCalendar(data) {
  const qs_ = data.quotes;
  if (!qs_.length) { $("#flex-result").innerHTML = `<div class="card">这段日期没有价格数据。</div>`; return; }
  const byDate = Object.fromEntries(qs_.map((q) => [q.depart_date, q]));
  const prices = qs_.map((q) => q.price), lo = Math.min(...prices), hi = Math.max(...prices);
  const dates = qs_.map((q) => q.depart_date).sort();
  let d = new Date(dates[0] + "T00:00:00Z"), end = new Date(dates[dates.length - 1] + "T00:00:00Z");
  let html = ["一", "二", "三", "四", "五", "六", "日"].map((w) => `<div class="dow">${w}</div>`).join("");
  let month = "";
  while (d <= end) {
    const iso = d.toISOString().slice(0, 10);
    if (iso.slice(0, 7) !== month) {
      month = iso.slice(0, 7);
      html += `<div class="month">${month.replace("-", " 年 ")} 月</div>`;
      const pad = (d.getUTCDay() + 6) % 7;
      html += `<div class="day empty"></div>`.repeat(pad);
    }
    const q = byDate[iso];
    html += q
      ? `<div class="day" data-date="${iso}" style="background:${heat((q.price - lo) / (hi - lo || 1))}" title="${esc(q.source)}"><div class="d">${+iso.slice(8)}</div><div class="p">${money(q.price, data.currency)}</div></div>`
      : `<div class="day empty"><div class="d">${+iso.slice(8)}</div></div>`;
    d.setUTCDate(d.getUTCDate() + 1);
  }
  const cheapest = data.cheapest.map((q) => `<tr><td>${q.depart_date}${q.return_date ? " → " + q.return_date : ""}</td><td class="num">${money(q.price, data.currency)}</td>
    <td class="num">${data.median ? Math.round((1 - q.price / data.median) * 100) + "%" : ""}</td><td><button class="secondary" data-date="${q.depart_date}">查实时价</button></td></tr>`).join("");
  $("#flex-result").innerHTML = `
    <div class="card"><h2>${esc(data.origin)} → ${esc(data.destination)} 每日最低价</h2>
      <div class="meta">${data.live ? "实时价" : "缓存价，仅用于挑日期；点某一天会查实时价"}。中位数 ${money(data.median, data.currency)}。</div>
      <div class="calendar">${html}</div>
      <div class="legend">便宜 <span style="background:${heat(0)}"></span><span style="background:${heat(0.5)}"></span><span style="background:${heat(1)}"></span> 贵</div></div>
    <div class="card"><h2>最便宜的 5 天</h2><table><thead><tr><th>日期</th><th class="num">价格</th><th class="num">低于中位数</th><th></th></tr></thead><tbody>${cheapest}</tbody></table></div>`;
  $$("#flex-result [data-date]").forEach((el) => el.addEventListener("click", () => searchFromDate(data.origin, data.destination, el.dataset.date, data.trip_days)));
}

function searchFromDate(origin, destination, date, tripDays) {
  searchForm.origin.value = origin;
  searchForm.destination.value = destination;
  searchForm.depart_date.value = date;
  if (tripDays) {
    const r = new Date(date + "T00:00:00Z"); r.setUTCDate(r.getUTCDate() + +tripDays);
    searchForm.return_date.value = isoDate(r);
  } else searchForm.return_date.value = "";
  switchTab("search");
  searchForm.requestSubmit();
}

// ---------- 任意目的地 ----------
$("#anywhere-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const p = formData(ev.target);
  showMsg("#anywhere-msg", "");
  loading("#anywhere-result");
  try {
    const data = await api("/api/anywhere?" + qs(p));
    showMsg("#anywhere-msg", data.errors.join("；"));
    const rows = data.quotes.map((q) => `<tr><td><b>${esc(q.destination)}</b></td><td>${q.depart_date}${q.return_date ? " → " + q.return_date : ""}</td>
      <td>${q.transfers === 0 ? "直飞" : q.transfers ? `中转 ${q.transfers} 次` : ""}</td><td>${esc(q.airline)}</td><td class="num">${money(q.price, data.currency)}</td>
      <td><button class="secondary" data-dest="${esc(q.destination)}" data-date="${q.depart_date}" data-ret="${q.return_date || ""}">查实时价</button></td></tr>`).join("");
    $("#anywhere-result").innerHTML = data.quotes.length
      ? `<div class="card"><h2>从 ${esc(data.origin)} 出发最便宜的地方</h2><div class="meta">缓存价，按价格排序</div>
         <table><thead><tr><th>目的地</th><th>日期</th><th>中转</th><th>航司</th><th class="num">价格</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : "";
    $$("#anywhere-result [data-dest]").forEach((b) => b.addEventListener("click", () => {
      const days = b.dataset.ret ? Math.round((new Date(b.dataset.ret) - new Date(b.dataset.date)) / 86400000) : null;
      searchFromDate(data.origin, b.dataset.dest, b.dataset.date, days);
    }));
  } catch (e) {
    $("#anywhere-result").innerHTML = "";
    showMsg("#anywhere-msg", e.message, true);
  }
});

// ---------- 航线监控 ----------
const watchForm = $("#watch-form");
watchForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const p = formData(watchForm);
  for (const k of ["trip_days", "target_price"]) if (p[k]) p[k] = +p[k];
  showMsg("#watch-msg", "");
  loading("#watch-msg", "正在添加并第一次查价…");
  try {
    const res = await api("/api/watches", { method: "POST", body: JSON.stringify(p) });
    const fc = res.first_check;
    showMsg("#watch-msg", fc.status === "ok" ? `已添加。当前最低 ${money(fc.best_price)}（${fc.best_date} 出发）。` : `已添加。${fc.message || ""}`);
    loadWatches();
  } catch (e) {
    showMsg("#watch-msg", e.message, true);
  }
});

function prefillWatch(id) {
  const it = state.lastSearch.itineraries.find((i) => i.id === id);
  const q = state.lastSearch.query;
  const d = new Date(q.depart_date + "T00:00:00Z");
  const from = new Date(d), to = new Date(d);
  from.setUTCDate(from.getUTCDate() - 3); to.setUTCDate(to.getUTCDate() + 3);
  watchForm.origin.value = q.origin;
  watchForm.destination.value = q.destination;
  watchForm.depart_from.value = isoDate(from) < addDays(0) ? addDays(0) : isoDate(from);
  watchForm.depart_to.value = isoDate(to);
  watchForm.trip_days.value = q.return_date ? Math.round((new Date(q.return_date) - d) / 86400000) : "";
  watchForm.target_price.value = it ? Math.round(price(it) * 0.85) : "";
  switchTab("watches");
  showMsg("#watch-msg", "已按这个航班填好（出发日 ±3 天，目标价为当前价的 85%），确认后点「添加监控」。");
}

async function loadWatches() {
  try {
    const ws = await api("/api/watches");
    if (!ws.length) { $("#watch-list").innerHTML = `<div class="card meta">还没有监控的航线。</div>`; return; }
    $("#watch-list").innerHTML = ws.map((w) => `<div class="card" id="w-${w.id}">
      <div class="itin"><div><b>${esc(w.origin)} → ${esc(w.destination)}</b>
        <div class="meta">${w.depart_from} 至 ${w.depart_to} 出发 · ${w.trip_days ? `停留 ${w.trip_days} 天` : "单程"}${w.target_price ? ` · 目标价 ${money(w.target_price)}` : ""}</div>
        <div class="meta">${w.last_checked ? `上次检查 ${w.last_checked.slice(0, 16).replace("T", " ")} UTC` : "尚未检查"}</div></div>
        <div class="price">${money(w.last_price)}<small>${w.last_price_date ? w.last_price_date + " 出发" : ""}</small></div>
        <div class="buttons"><button class="secondary" data-check="${w.id}">立即检查</button><button class="secondary" data-history="${w.id}">价格历史</button><button class="link" data-del="${w.id}">删除</button></div>
      </div><div class="history"></div></div>`).join("");
    $$("[data-check]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = "检查中…";
      try {
        const r = await api(`/api/watches/${b.dataset.check}/check`, { method: "POST" });
        showMsg("#watch-msg", r.alert ? `发现特价：${r.alert}${r.sent.length ? "（已发送提醒）" : "（未配置提醒渠道）"}` : r.status === "ok" ? "检查完成，暂无特价。" : r.message);
      } catch (e) { showMsg("#watch-msg", e.message, true); }
      loadWatches();
    }));
    $$("[data-history]").forEach((b) => b.addEventListener("click", () => showHistory(+b.dataset.history)));
    $$("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      await api(`/api/watches/${b.dataset.del}`, { method: "DELETE" });
      loadWatches();
    }));
  } catch (e) {
    showMsg("#watch-msg", e.message, true);
  }
}

async function showHistory(id) {
  const rows = await api(`/api/watches/${id}/history`);
  const el = $(`#w-${id} .history`);
  if (!rows.length) { el.innerHTML = `<p class="meta">还没有历史数据。</p>`; return; }
  const byCheck = {};
  for (const r of rows) byCheck[r.checked_at] = Math.min(byCheck[r.checked_at] ?? Infinity, r.price);
  const pts = Object.entries(byCheck);
  const W = 640, H = 140, L = 56, R = 12, T = 10, B = 26;
  const vals = pts.map((p) => p[1]), lo = Math.min(...vals) * 0.95, hi = Math.max(...vals) * 1.05;
  const X = (i) => L + (pts.length === 1 ? (W - L - R) / 2 : i / (pts.length - 1) * (W - L - R));
  const Y = (v) => H - B - (v - lo) / (hi - lo || 1) * (H - B - T);
  el.innerHTML = `<h2>每次检查的最低价</h2><svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="价格历史">
    <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>
    <text x="${L - 6}" y="${Y(hi / 1.05) + 4}" text-anchor="end">${money(hi / 1.05)}</text>
    <text x="${L - 6}" y="${Y(lo / 0.95) + 4}" text-anchor="end">${money(lo / 0.95)}</text>
    <polyline class="frontline" style="stroke-dasharray:none" points="${pts.map((p, i) => `${X(i)},${Y(p[1])}`).join(" ")}"/>
    ${pts.map((p, i) => `<circle class="dot front" cx="${X(i)}" cy="${Y(p[1])}" r="3.5"><title>${p[0].slice(0, 16)} ${money(p[1])}</title></circle>`).join("")}
    <text x="${L}" y="${H - 6}">${pts[0][0].slice(0, 10)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${pts[pts.length - 1][0].slice(0, 10)}</text>
  </svg>`;
}

$("#test-alert").addEventListener("click", async () => {
  const r = await api("/api/test-alert", { method: "POST" });
  showMsg("#watch-msg", r.sent.length ? `已通过 ${r.sent.join("、")} 发送测试提醒` : "还没有配置提醒渠道：在 .env 填写邮箱（SMTP）或 Telegram 机器人");
});

// ---------- 特价网站 ----------
$("#deals-form").addEventListener("submit", (ev) => { ev.preventDefault(); loadDeals(); });

async function loadDeals() {
  const airports = $("#deals-form [name=airports]").value || state.home;
  loading("#deals-list", "正在读取特价网站…");
  showMsg("#deals-msg", "");
  try {
    const data = await api("/api/deals?" + qs({ airports }));
    showMsg("#deals-msg", data.errors.join("；"));
    $("#deals-list").innerHTML = data.deals.length ? data.deals.map((d) => `<div class="card">
      ${d.matched.length ? `<span class="tag good">相关：${esc(d.matched.join("、"))}</span>` : ""}
      <a href="${esc(d.link)}" target="_blank" rel="noopener"><b>${esc(d.title)}</b></a>
      <div class="meta">${esc(d.source)}${d.published ? " · " + d.published.slice(0, 16).replace("T", " ") : ""}</div>
      <div class="meta">${esc(d.summary.slice(0, 180))}</div></div>`).join("") : `<div class="card meta">没有读到特价帖子。</div>`;
  } catch (e) {
    $("#deals-list").innerHTML = "";
    showMsg("#deals-msg", e.message, true);
  }
}

// ---------- 初始化 ----------
(function init() {
  searchForm.depart_date.value = addDays(30);
  $("#flex-form [name=start]").value = addDays(14);
  $("#flex-form [name=end]").value = addDays(56);
  $("#watch-form [name=depart_from]").value = addDays(14);
  $("#watch-form [name=depart_to]").value = addDays(45);
  const next = new Date(); next.setMonth(next.getMonth() + 1);
  $("#anywhere-form [name=month]").value = isoDate(next).slice(0, 7);
  $("#hv-out").textContent = money(15);
  loadStatus();
  let tab = "search";
  try { tab = localStorage.getItem("tab") || "search"; } catch (e) { /* 忽略 */ }
  switchTab(tab);
})();
