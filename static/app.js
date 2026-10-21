"use strict";
/* Снабженец-ассистент: интерфейс. Без сборки и зависимостей. */

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const STATUS = {new: "Новый", contacted: "Написал", quote: "Получил КП", agreed: "Договорились", rejected: "Отказ"};

const store = {   // localStorage может быть недоступен (приватный режим): страница должна работать и без него
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ничего */ } },
};

const state = {
  health: null, searchId: null, search: null, results: [], view: "cards", quotes: false, showHidden: false,
  selected: new Set(), compareOnly: false, sort: "score", find: "", weights: {}, poll: null,
  ctx: store.get("ctx", {category: "", region: ""}), letterDomain: null, tab: "search", lastSig: "",
  bulk: {emails: [], marked: false}, offers: {detail: null},
};

/* ---------- сеть ---------- */
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let m = r.statusText;
    try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* тело не JSON */ }
    throw new Error(m);
  }
  return r;
}
const jget = p => api(p).then(r => r.json());
const jpost = (p, b, method = "POST") => api(p, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(b)}).then(r => r.json());

/* ---------- форматирование ---------- */
const fmtDate = iso => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ""); return m ? `${m[3]}.${m[2]}.${m[1]}` : ""; };
const CUR = {RUB: "₽", USD: "$", EUR: "€"};
const money = (n, cur) => `${Number(n).toLocaleString("ru-RU", {maximumFractionDigits: 2})} ${CUR[cur || "RUB"] || cur}`;
const plural = (n, a, b, c) => { const m = n % 100, k = n % 10; return (m > 10 && m < 20) ? c : k === 1 ? a : (k >= 2 && k <= 4) ? b : c; };

function toast(text) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = text; t.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2600);
}
async function copyText(text, okMessage = "Скопировано") {
  try { await navigator.clipboard.writeText(text); toast(okMessage); }
  catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast(okMessage); }
}

/* ---------- факты: значение + откуда взято ---------- */
function fact(f) {
  if (!f) return `<span class="miss">не указано — уточнить</span>`;
  const src = f.url
    ? `<a class="src" href="${esc(f.url)}" target="_blank" rel="noopener" title="${esc(f.quote)}">источник ↗</a>`
    : `<span class="src from-kp" title="Данные из загруженного КП">из КП</span>`;
  return `<span>${esc(f.value)}</span>${src}${f.quote ? `<div class="quote">«${esc(f.quote)}»</div>` : ""}`;
}
const facts = list => (list && list.length) ? list.map(f => `<div>${fact(f)}</div>`).join("") : `<span class="miss">не указано — уточнить</span>`;
function regionsHtml(d) {
  const items = [...(d.regions || [])];
  if (d.nationwide) items.push({...d.nationwide, value: "По всей России"});
  return facts(items);
}
/* ---------- цены на искомый товар ---------- */
const num = n => Number(n).toLocaleString("ru-RU", {maximumFractionDigits: 2});
/* цена как написана на сайте: единица только если стояла рядом с ценой, иначе показываем фасовку */
/* единицу чистим от «₽», «руб», «/», «за»: в старых данных она могла быть записана как «₽/кг» */
const cleanUnit = u => String(u || "").replace(/₽|руб\w*\.?|за\b|\//gi, "").trim();
const priceParts = p => cleanUnit(p.unit)
  ? {main: `${num(p.price)} ₽/${cleanUnit(p.unit)}`, note: ""}
  : {main: `${num(p.price)} ₽`, note: `${p.pack ? `фасовка ${p.pack} · ` : ""}ед. цены не указана`};
const priceText = p => { const {main, note} = priceParts(p); return note ? `${main} · ${note}` : main; };
/* цена за кг или литр для сравнения: написана на сайте или рассчитана из фасовки (тогда помечено) */
const basePriceText = p => `${num(p.base_price)} ₽/${p.base_unit}${p.derived ? " (расчёт)" : ""}`;

/* цены у поставщика: из выдачи поиска, у шорт-листа — последние извлечённые */
function queryPricesOf(it) { return it.query_prices ?? it.data?.query_prices ?? null; }
function queryNameOf(it) { return it.query_prices_for || it.data?.query_for || state.ctx.category || "искомый товар"; }

/* сравнивать можно цены за кг с ценами за кг; за литр с литром. Берём единицу, которой больше всего среди результатов */
function dominantUnit() {
  const count = {};
  for (const r of state.results) for (const p of (queryPricesOf(r) || [])) if (p.base_unit) count[p.base_unit] = (count[p.base_unit] || 0) + 1;
  return Object.entries(count).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
}
function bestPrice(it, unit) {      // самая низкая цена за кг/л у поставщика
  const items = (queryPricesOf(it) || []).filter(p => p.base_price != null && p.base_unit === unit);
  return items.length ? items.reduce((a, b) => (b.base_price < a.base_price ? b : a)) : null;
}
/* подпись лучшей цены: «от 310 ₽/л» для сравнимых, иначе цена как на сайте */
function bestLabel(it) {
  const qp = queryPricesOf(it) || [];
  const best = bestPrice(it, dominantUnit());
  if (best) return `от ${basePriceText(best)}`;
  const cheapest = qp.reduce((a, b) => (b.price < a.price ? b : a));
  return `от ${priceText(cheapest)}`;
}
function queryPricesHtml(it) {
  const qp = queryPricesOf(it);
  if (qp === null) return fact(it.data.price_info);          // старые данные: запрос не учитывался
  if (!qp.length) return `<span class="miss">цен на сайте нет: запросить КП</span>`;
  const rows = qp.slice(0, 4).map(p => `<div class="qp"><span>${esc(p.name)}</span> <b>${esc(priceParts(p).main)}</b>
    ${priceParts(p).note ? `<span class="dom">${esc(priceParts(p).note)}</span>` : ""}
    ${p.base_price != null && p.derived ? `<span class="dom" title="Рассчитано из цены и фасовки, не указано на сайте">≈ ${esc(basePriceText(p))}</span>` : ""}
    <a class="src" href="${esc(p.url)}" target="_blank" rel="noopener" title="${esc(p.quote)}">источник ↗</a>
    <div class="quote">«${esc(p.quote)}»</div></div>`).join("");
  return rows + (qp.length > 4 ? `<div class="dom">и ещё ${qp.length - 4}</div>` : "");
}

function priceBadge(it) {
  const qp = queryPricesOf(it);
  if (qp === null) return "";
  if (!qp.length) return `<div class="price-badge none">цен на сайте нет: запросите КП</div>`;
  return `<div class="price-badge">${esc(bestLabel(it))}${qp.length > 1 ? ` · позиций: ${qp.length}` : ""}</div>`;
}

function missingOf(it) {
  const d = it.data ?? it;
  const qp = it.data ? queryPricesOf(it) : null;
  const m = [];
  if (!d.regions?.length && !d.nationwide && !d.address) m.push("работаете ли вы с нашим регионом");
  if (!d.min_order) m.push("минимальный объём заказа");
  if (!d.certificates?.length) m.push("сертификаты/декларации на продукцию");
  if (!d.delivery) m.push("условия и стоимость доставки");
  if (qp === null ? !d.price_info : !qp.length) m.push("прайс-лист / цены");
  return m;
}
const field = (label, html) => `<div class="f"><dt>${label}</dt><dd>${html}</dd></div>`;

/* ---------- веса критериев ---------- */
function defaultWeights() { return Object.fromEntries((state.health?.criteria || []).map(c => [c.key, c.default])); }
function weightsParam() {
  const def = defaultWeights();
  const changed = Object.keys(def).some(k => Number(state.weights[k] ?? def[k]) !== Number(def[k]));
  return changed ? Object.keys(def).map(k => `${k}:${state.weights[k] ?? def[k]}`).join(",") : "";
}
function renderWeights() {
  const def = defaultWeights();
  const rows = (state.health?.criteria || []).map(c => {
    const v = state.weights[c.key] ?? c.default;
    return `<label class="wrow"><span>${esc(c.label)}</span><input type="range" min="0" max="10" step="1" value="${v}" data-w="${c.key}"><output>${v}</output></label>`;
  }).join("");
  $("#weights-panel").innerHTML = `<div class="dom" style="margin-bottom:8px">Двигайте ползунки: оценки и порядок поставщиков пересчитаются сразу. 0 — критерий не важен.</div>${rows}<button class="ghost" id="weights-reset">Сбросить</button>`;
  void def;
  markWeights();
}
function markWeights() {   // видно, что оценки считаются не по умолчанию
  const changed = !!weightsParam();
  $("#weights-toggle").textContent = changed ? "Что для вас важнее (изменено)" : "Что для вас важнее";
  $("#weights-toggle").classList.toggle("changed", changed);
}
let weightsTimer;
function onWeightInput(e) {
  const el = e.target.closest("input[data-w]"); if (!el) return;
  el.nextElementSibling.textContent = el.value;
  state.weights[el.dataset.w] = Number(el.value);
  store.set("weights", state.weights);
  markWeights();
  clearTimeout(weightsTimer);
  weightsTimer = setTimeout(() => { loadResults().then(() => { render(); updateHash(); updateExportLinks(); }); }, 250);
}

/* ---------- карточка поставщика ---------- */
function breakdownHtml(sc) {
  if (!sc.breakdown?.length) return "";
  const rows = sc.breakdown.map(b => {
    const pct = b.max ? Math.round(b.points / b.max * 100) : 0;
    return `<tr><td>${esc(b.label)}</td><td class="dom">${esc(b.note)}</td>
      <td class="bar"><span style="width:${pct}%"></span></td><td class="num">${b.points}<span class="dom"> из ${b.max}</span></td></tr>`;
  }).join("");
  return `<table class="breakdown"><tbody>${rows}</tbody></table>
    <p class="dom">Оценка считается по прозрачным правилам из данных с сайта. Вес критериев можно поменять кнопкой «Что для вас важнее».</p>`;
}
function freshness(it) {
  const d = fmtDate(it.updated_at);
  const refresh = (state.health?.live_available && it.data.url) ? ` · <button class="linkish" data-act="refresh">обновить</button>` : "";
  return d ? `<span class="dom">Данные от ${d}${refresh}</span>` : "";
}

function cardHtml(it) {
  const d = it.data, sc = it.score;
  const cls = sc.score >= 70 ? "hi" : sc.score >= 45 ? "mid" : "lo";
  const name = d.name ? esc(d.name.value) : esc(d.domain);
  const why = (sc.pros.length ? `<div class="p">+ ${sc.pros.map(esc).join(" · ")}</div>` : "") +
              (sc.cons.length ? `<div class="c">− ${sc.cons.map(esc).join(" · ")}</div>` : "");
  const inShort = it.shortlisted, sel = state.selected.has(d.domain);
  const link = d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.domain)}</a>` : `<span>${esc(d.domain)}</span>`;
  return `<article class="card ${sc.passes ? "" : "dim"}" data-domain="${esc(d.domain)}">
    <div class="card-head">
      <div class="score ${cls}" title="Оценка по правилам, 0–100">${sc.score}</div>
      <div style="flex:1;min-width:0">
        <h3>${name}</h3>
        <div class="dom">${link}${d.also_domains?.length ? " · также: " + esc(d.also_domains.join(", ")) : ""}</div>
        ${priceBadge(it)}
        <div class="why">${why}</div>
        ${sc.passes ? "" : `<div class="fail">Не подходит по фильтрам: ${esc(sc.fail_reasons.join("; "))}</div>`}
      </div>
      <label class="pick" title="Выбрать для сравнения"><input type="checkbox" data-act="select" ${sel ? "checked" : ""}><span>сравнить</span></label>
    </div>
    <dl class="grid">
      ${field("Что поставляют", fact(d.offering))}
      ${field("Телефон", facts(d.phones))}
      ${field("Email", facts(d.emails))}
      ${field("Адрес", fact(d.address))}
      ${field("Регион работы", regionsHtml(d))}
      ${field("Минимальный заказ", fact(d.min_order))}
      ${field(`Цены на «${esc(queryNameOf(it))}»`, queryPricesHtml(it))}
      ${queryPricesOf(it) !== null && d.price_info ? field("Общие условия по ценам", fact(d.price_info)) : ""}
      ${field("Документы", facts(d.certificates))}
      ${field("Доставка", fact(d.delivery))}
      ${field("Опт / юрлица", fact(d.wholesale))}
      ${d.inn ? field("ИНН", fact(d.inn)) : ""}
    </dl>
    <details class="how"><summary>Как посчитана оценка</summary>${breakdownHtml(sc)}</details>
    <div class="actions">
      <button class="ghost ${inShort ? "on" : ""}" data-act="short">${inShort ? "★ В шорт-листе" : "☆ В шорт-лист"}</button>
      <button class="ghost" data-act="letter">Письмо с запросом КП</button>
      <button class="ghost" data-act="offers">КП${it.offers_count ? ` (${it.offers_count})` : ": загрузить"}</button>
      ${freshness(it)}
      <span class="dom">${d.rejected_facts ? `Отброшено непроверенных данных: ${d.rejected_facts}` : ""}</span>
    </div>
  </article>`;
}

function tableHtml(items) {
  const cut = s => s.length > 90 ? esc(s.slice(0, 88)) + "…" : esc(s);
  const short = f => f ? `<span title="${esc(f.value)}">${cut(f.value)}</span>` : `<span class="miss">уточнить</span>`;
  const rows = items.map(it => {
    const d = it.data, sc = it.score;
    const regs = [...(d.regions || []).map(r => r.value), ...(d.nationwide ? ["вся Россия"] : [])].join(", ") || (d.address ? d.address.value : "");
    const contacts = [...(d.phones || []).map(p => esc(p.value)), ...(d.emails || []).map(e => esc(e.value))].join("<br>");
    return `<tr class="${sc.passes ? "" : "dim"}">
      <td><b>${esc(d.name ? d.name.value : d.domain)}</b><div class="dom">${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.domain)}</a>` : esc(d.domain)}</div></td>
      <td><b>${sc.score}</b></td>
      <td>${regs ? `<span title="${esc(regs)}">${cut(regs)}</span>` : '<span class="miss">уточнить</span>'}</td>
      <td>${short(d.min_order)}</td><td>${tablePrice(it, short)}</td>
      <td>${(d.certificates || []).length ? esc(d.certificates.map(c => c.value).join(", ")) : '<span class="miss">уточнить</span>'}</td>
      <td>${short(d.delivery)}</td>
      <td>${contacts || '<span class="miss">уточнить</span>'}</td>
    </tr>`;
  }).join("");
  return `<div class="tablewrap"><table><thead><tr><th>Поставщик</th><th>Оценка</th><th>Регион</th><th>Мин. заказ</th><th>Цены на запрос</th><th>Документы</th><th>Доставка</th><th>Контакты</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function tablePrice(it, short) {
  const qp = queryPricesOf(it);
  if (qp === null) return short(it.data.price_info);
  if (!qp.length) return `<span class="miss">нет на сайте, запросить КП</span>`;
  return `<b>${esc(bestLabel(it))}</b>${qp.length > 1 ? `<div class="dom">позиций: ${qp.length}</div>` : ""}`;
}

/* ---------- выдача: сортировка, поиск, сравнение выбранных ---------- */
function visibleResults() {
  let list = state.results.filter(r => r.score.passes || state.showHidden || state.compareOnly);
  if (state.compareOnly) list = list.filter(r => state.selected.has(r.domain));
  const q = state.find.trim().toLowerCase();
  if (q) list = list.filter(r => JSON.stringify([r.domain, r.data.name, r.data.offering, r.data.address, r.data.regions, r.data.certificates]).toLowerCase().includes(q));
  const byName = r => (r.data.name?.value || r.domain).toLowerCase();
  if (state.sort === "min") list = [...list].sort((a, b) => (a.data.min_order_kg ?? 1e12) - (b.data.min_order_kg ?? 1e12));
  if (state.sort === "name") list = [...list].sort((a, b) => byName(a).localeCompare(byName(b), "ru"));
  if (state.sort === "price") {       // по самой низкой цене в преобладающей единице; без цены в конце
    const unit = dominantUnit(), key = r => bestPrice(r, unit)?.base_price ?? 1e12;
    list = [...list].sort((a, b) => key(a) - key(b));
  }
  return list;
}

function renderCompareBar() {
  const bar = $("#compare-bar"), n = state.selected.size;
  bar.hidden = !n && !state.compareOnly;
  if (bar.hidden) return;
  bar.innerHTML = state.compareOnly
    ? `Сравниваем выбранных: <b>${n}</b> <button class="ghost" data-bar="all">Показать всех</button>`
    : `Выбрано: <b>${n}</b> ${n < 2 ? '<span class="dom">отметьте ещё хотя бы одного</span>' : ""}
       <button class="primary" data-bar="compare" ${n < 2 ? "disabled" : ""}>Сравнить выбранных</button>
       <button class="ghost" data-bar="clear">Сбросить</button>`;
}

function render() {
  const all = state.results;
  const hiddenN = all.filter(r => !r.score.passes).length;
  const tb = $("#hidden-toggle");
  tb.hidden = !hiddenN || state.compareOnly;
  tb.textContent = state.showHidden ? "Скрыть не прошедших фильтры" : `Не прошли фильтры: ${hiddenN}, показать`;
  renderCompareBar();
  const box = $("#results");
  box.className = state.quotes ? "show-quotes" : "";
  const shown = visibleResults();
  const running = state.search?.status === "running";
  if (!shown.length) {
    box.innerHTML = running ? "" : `<div class="empty">${all.length ? "Ничего не найдено среди результатов. Измените поиск или фильтры." : "Нет подходящих поставщиков. Ослабьте фильтры или измените запрос."}</div>`;
    return;
  }
  box.innerHTML = (state.view === "table" || state.compareOnly) ? tableHtml(shown) : `<div class="cards">${shown.map(cardHtml).join("")}</div>`;
}

/* ---------- поиск ---------- */
function setStatus(text, err = false) { const s = $("#status"); s.textContent = text; s.className = "status" + (err ? " err" : ""); }
function setProgress(done, total) {
  const p = $("#progress"); p.hidden = !(total > 0);
  $("#progress-bar").style.width = total ? `${Math.round(done / total * 100)}%` : "0";
}

function currentFilters() {
  return {need_certs: $("#f-certs").checked, max_min_order_kg: $("#f-min").value ? Number($("#f-min").value) : null,
          delivery_to_region: $("#f-deliv").checked};
}
function pushRecent(category, region, demo) {
  const list = store.get("recent", []).filter(r => !(r.category === category && r.region === region));
  list.unshift({category, region, demo});
  store.set("recent", list.slice(0, 6));
  renderRecent();
}
function renderRecent() {
  const list = store.get("recent", []);
  $("#recent-row").hidden = !list.length;
  $("#recent").innerHTML = list.map((r, i) => `<button type="button" class="chip" data-recent="${i}">${esc(r.category)}${r.region ? " · " + esc(r.region) : ""}</button>`).join(" ");
}

function updateHash() {
  if (!state.searchId) return;
  const w = weightsParam();
  const h = `#s=${state.searchId}${w ? "&w=" + encodeURIComponent(w) : ""}`;
  if (location.hash !== h) history.replaceState(null, "", h);   // replaceState не вызывает hashchange
}

async function startSearch(e) {
  e?.preventDefault();
  clearInterval(state.poll);
  const body = {category: $("#category").value.trim(), region: $("#region").value.trim(), demo: $("#demo").checked, filters: currentFilters()};
  state.ctx = {category: body.category, region: body.region}; store.set("ctx", state.ctx);
  state.selected.clear(); state.compareOnly = false; state.results = []; state.search = null; state.lastSig = "";
  $("#go").disabled = true; $("#toolbar").hidden = true; $("#results").innerHTML = ""; $("#weights-panel").hidden = true;
  setProgress(0, 0); setStatus("Запускаю поиск…");
  try {
    const {search_id} = await jpost("/api/search", body);
    state.searchId = search_id; pushRecent(body.category, body.region, body.demo); updateHash();
    state.poll = setInterval(poll, 1200); poll();
  } catch (err) { setStatus(err.message, true); $("#go").disabled = false; }
}

async function loadResults() {
  const w = weightsParam();
  const r = await jget(`/api/search/${state.searchId}${w ? "?w=" + encodeURIComponent(w) : ""}`);
  state.search = r.search; state.results = r.results;
  return r;
}

async function poll() {
  try {
    const r = await loadResults(), s = r.search;
    const sig = s.status + "|" + r.results.map(x => x.domain + x.score.score).join(",");
    if (s.status === "running") {
      setStatus(s.progress || "Идёт поиск…"); setProgress(s.done, s.total);
      $("#toolbar").hidden = !r.results.length;
      if (sig !== state.lastSig) { state.lastSig = sig; render(); }
      return;
    }
    clearInterval(state.poll); $("#go").disabled = false; setProgress(0, 0);
    if (s.status === "error") { setStatus(s.error, true); return; }
    setStatus(s.progress); finishSearch();
  } catch (err) { clearInterval(state.poll); setStatus(err.message, true); $("#go").disabled = false; }
}

function updateExportLinks() {
  const w = weightsParam() ? "&w=" + encodeURIComponent(weightsParam()) : "";
  $("#x-xlsx").href = `/api/export?search_id=${state.searchId}&format=xlsx${w}`;
  $("#x-csv").href = `/api/export?search_id=${state.searchId}&format=csv${w}`;
}
function finishSearch() {
  $("#toolbar").hidden = false;
  updateExportLinks(); renderWeights(); render(); updateHash();
}

/* ссылка на результаты: #s=ID[&w=веса] */
async function openFromHash() {
  const m = /[#&]s=(\d+)/.exec(location.hash);
  if (!m) return;
  const wm = /[#&]w=([^&]+)/.exec(location.hash);
  state.weights = {};     // ссылка однозначна: чего нет в ней, то по умолчанию, а не из настроек этого браузера
  if (wm) for (const part of decodeURIComponent(wm[1]).split(",")) { const [k, v] = part.split(":"); if (k && !isNaN(Number(v))) state.weights[k] = Number(v); }
  renderWeights();
  state.searchId = Number(m[1]);
  try {
    const r = await loadResults(), s = r.search;
    $("#category").value = s.category; $("#region").value = s.region;
    $("#f-certs").checked = !!s.filters.need_certs; $("#f-deliv").checked = !!s.filters.delivery_to_region;
    $("#f-min").value = s.filters.max_min_order_kg ?? ""; $("#demo").checked = s.mode !== "live" || !state.health.live_available;
    state.ctx = {category: s.category, region: s.region}; store.set("ctx", state.ctx);
    if (s.status === "running") { $("#go").disabled = true; state.poll = setInterval(poll, 1200); poll(); }
    else if (s.status === "error") setStatus(s.error, true);
    else { setStatus(s.progress); finishSearch(); }
  } catch { setStatus("Результаты по этой ссылке не найдены. Возможно, они устарели.", true); }
}

/* ---------- шорт-лист ---------- */
async function toggleShort(domain) {
  const it = state.results.find(r => r.domain === domain);
  const on = !it.shortlisted;
  await jpost(`/api/supplier/${encodeURIComponent(domain)}`, {shortlisted: on}, "PATCH");
  it.shortlisted = on; render(); refreshShortCount();
}
async function refreshShortCount() {
  const {items} = await jget("/api/shortlist");
  $("#short-n").textContent = items.length ? `(${items.length})` : "";
  return items;
}
async function renderShort() {
  const items = await refreshShortCount();
  const box = $("#short-list");
  $("#bulk-open").hidden = !items.length;
  if (!items.length) { box.innerHTML = `<div class="empty">Пока пусто. Добавляйте поставщиков звёздочкой в результатах поиска.</div>`; return; }
  box.innerHTML = `<div class="cards">${items.map(it => {
    const d = it.data;
    const contacts = [...(d.phones || []).map(p => esc(p.value)), ...(d.emails || []).map(e => esc(e.value))].join(" · ") || "контакты не найдены";
    return `<article class="card" data-domain="${esc(d.domain)}">
      <h3>${esc(d.name ? d.name.value : d.domain)} <span class="dom">${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.domain)}</a>` : esc(d.domain)}</span></h3>
      <div class="dom">${contacts}</div>
      ${(queryPricesOf(it) || []).length ? `<div class="price-line">Цены на «${esc(queryNameOf(it))}»: ${queryPricesOf(it).slice(0, 3).map(p => `${esc(p.name)} <b>${esc(priceText(p))}</b>`).join("; ")}</div>` : ""}
      <div class="row">
        <select data-act="status">${Object.entries(STATUS).map(([k, v]) => `<option value="${k}" ${it.status === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <button class="ghost" data-act="letter">Письмо</button>
        <button class="ghost" data-act="offers">КП${it.offers_count ? ` (${it.offers_count})` : ": загрузить"}</button>
        <button class="ghost" data-act="unshort">Убрать</button>
        ${freshness(it)}
      </div>
      <label style="margin-top:10px">Заметки</label>
      <textarea rows="2" data-act="note" placeholder="Кому писали, что ответили, цена…">${esc(it.note)}</textarea>
    </article>`; }).join("")}</div>`;
}

/* ---------- письма ---------- */
async function openLetter(domain, ai = false) {
  state.letterDomain = domain;
  const known = state.results.find(r => r.domain === domain);
  const row = known || (await jget("/api/shortlist")).items.find(r => r.domain === domain);
  const l = await jpost("/api/letter", {domain, category: state.ctx.category, region: state.ctx.region, missing: missingOf(row), ai});
  $("#l-to").value = l.to; $("#l-subj").value = l.subject; $("#l-body").value = l.body;
  $("#modal").classList.add("open"); updateMailto();
}
function updateMailto() {
  $("#l-mail").href = `mailto:${encodeURIComponent($("#l-to").value)}?subject=${encodeURIComponent($("#l-subj").value)}&body=${encodeURIComponent($("#l-body").value)}`;
}

async function openBulk() {
  const l = await jpost("/api/letter/bulk", {category: state.ctx.category, region: state.ctx.region});
  state.bulk = {emails: l.recipients.map(r => r.email).filter(Boolean), marked: false};
  $("#b-subj").value = l.subject; $("#b-body").value = l.body;
  $("#bulk-recipients").innerHTML = "Кому: " + l.recipients.map(r => r.email ? `${esc(r.name)} (${esc(r.email)})` : `${esc(r.name)}: <span class="miss">нет email</span>`).join("; ");
  $("#bulk-modal").classList.add("open"); updateBulkMail();
}
function updateBulkMail() {
  $("#b-mail").href = `mailto:?bcc=${encodeURIComponent(state.bulk.emails.join(","))}&subject=${encodeURIComponent($("#b-subj").value)}&body=${encodeURIComponent($("#b-body").value)}`;
}
async function markBulkOnce() {
  if (state.bulk.marked || !$("#b-mark").checked) return;
  state.bulk.marked = true;
  await jpost("/api/letter/bulk", {category: state.ctx.category, region: state.ctx.region, mark_contacted: true});
  renderShort();
}

/* ---------- КП: рисуем результат, сравнение цен, историю ---------- */
function trendHtml(row) {
  const pct = row.delta_pct != null ? ` ${Math.abs(row.delta_pct).toLocaleString("ru-RU")}%` : "";
  const t = {
    up: `<span class="tr up">▲ +${pct.trim() || ""}</span>`, down: `<span class="tr down">▼ −${pct.trim() || ""}</span>`,
    same: `<span class="tr same">без изменений</span>`, new: `<span class="tr new">новая позиция</span>`,
    unit_changed: `<span class="tr warn" title="Единицы измерения в двух КП разные">другая ед. изм.</span>`,
  }[row.trend];
  return t;
}
function diffSummaryHtml(diff) {
  if (!diff.has_previous) return `<span class="dom">Это первое КП от поставщика: сравнивать пока не с чем.</span>`;
  const s = diff.summary, parts = [];
  if (s.up) parts.push(`<span class="tr up">▲ подорожало: ${s.up}</span>`);
  if (s.down) parts.push(`<span class="tr down">▼ подешевело: ${s.down}</span>`);
  if (s.same) parts.push(`<span class="tr same">без изменений: ${s.same}</span>`);
  if (s.new) parts.push(`<span class="tr new">новых: ${s.new}</span>`);
  if (s.removed) parts.push(`<span class="tr warn">убрано из прайса: ${s.removed}</span>`);
  const avg = s.avg_change_pct != null ? `<span class="dom"> · в среднем ${s.avg_change_pct > 0 ? "+" : ""}${s.avg_change_pct.toLocaleString("ru-RU")}%</span>` : "";
  return parts.join(" ") + avg;
}
function diffTableHtml(diff, cur) {
  if (!diff.has_previous || !diff.rows.length) return "";
  const rows = diff.rows.map(r => `<tr><td>${esc(r.name)}</td><td class="num">${r.prev_price != null ? money(r.prev_price, cur) : "—"}</td>
      <td class="num"><b>${money(r.price, cur)}</b><span class="dom"> / ${esc(r.unit || "?")}</span></td><td>${trendHtml(r)}</td></tr>`).join("");
  const removed = diff.removed.length ? `<p class="dom">Убрано из прайса: ${diff.removed.map(i => esc(i.name)).join("; ")}</p>` : "";
  const warn = (diff.warnings || []).map(w => `<p class="fail">${esc(w)}</p>`).join("");
  return `${warn}<div class="tablewrap"><table class="price"><thead><tr><th>Позиция</th><th>Было</th><th>Стало</th><th>Изменение</th></tr></thead><tbody>${rows}</tbody></table></div>${removed}`;
}
function itemsTableHtml(data) {
  const cur = data.currency;
  const rows = data.items.map(i => `<tr><td>${esc(i.name)}${i.pack ? `<div class="dom">${esc(i.pack)}</div>` : ""}</td>
      <td class="num"><b>${money(i.price, cur)}</b><span class="dom"> / ${esc(i.unit || "?")}</span></td>
      <td>${i.verified ? `<span class="ok" title="Цитата и цена найдены в тексте документа">✓ найдено в тексте</span>` : `<span class="dom" title="Скан или фото: проверить по тексту нельзя">не проверено по тексту</span>`}
        ${i.quote ? `<div class="quote q-always">«${esc(i.quote)}»</div>` : ""}</td></tr>`).join("");
  return `<div class="tablewrap"><table class="price"><thead><tr><th>Позиция</th><th>Цена</th><th>Проверка</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${data.rejected_items ? `<p class="dom">Не принято (не подтверждено текстом документа): ${data.rejected_items}</p>` : ""}`;
}
function termsHtml(data) {
  const t = (label, v) => v ? field(label, esc(v)) : "";
  const vat = data.vat_included === true ? "с НДС" : data.vat_included === false ? "без НДС" : "";
  return `<dl class="grid terms">${t("Минимальный заказ", data.min_order)}${t("Доставка", data.delivery)}${t("Оплата", data.payment)}
    ${t("Цены", vat)}${data.valid_until ? field("Действует до", fmtDate(data.valid_until)) : ""}</dl>`;
}

function offerCardHtml(res) {
  const {offer, match} = res, d = offer.data, sup = d.supplier;
  const assigned = offer.domain;
  const meta = [offer.filename, d.offer_date ? "от " + fmtDate(d.offer_date) : "", d.currency && d.currency !== "RUB" ? d.currency : ""].filter(Boolean).join(" · ");
  let matchHtml;
  if (assigned) {
    const why = (match?.candidates || []).find(c => c.domain === assigned)?.reasons?.join(", ");
    matchHtml = `<div class="match ok-box">✓ Привязано к поставщику <b>${esc(assigned)}</b>${why ? ` <span class="dom">(${esc(why)})</span>` : ""}.
      Статус обновлён на «Получил КП». <button class="linkish" data-offer-open="${esc(assigned)}">Открыть историю</button></div>`;
  } else {
    const cands = (match?.candidates || []).map(c => `<div class="cand"><span><b>${esc(c.name)}</b> <span class="dom">${esc(c.domain)} · ${esc(c.reasons.join(", "))}</span></span>
        <button class="ghost" data-assign="${esc(c.domain)}" data-offer="${offer.id}">Это он</button></div>`).join("");
    matchHtml = `<div class="match warn-box"><b>Не удалось уверенно определить поставщика.</b> Выберите вручную или создайте нового.
      ${cands}
      <div class="row"><select data-pick="${offer.id}"><option value="">Другой поставщик из базы…</option></select>
        <button class="ghost" data-assign-pick="${offer.id}">Привязать</button>
        <button class="primary" data-create="${offer.id}">Создать «${esc(sup.name || "нового поставщика")}»</button></div></div>`;
  }
  return `<article class="card offer-card" data-offer-card="${offer.id}">
    <div class="card-head"><div class="score kp">КП</div><div style="flex:1;min-width:0">
      <h3>${esc(sup.name || "Поставщик не указан в документе")}</h3>
      <div class="dom">${esc(meta)}${offer.demo ? ' · <span class="demo-badge">пример, данные вымышлены</span>' : ""}</div>
      ${d.summary ? `<p class="why">${esc(d.summary)}</p>` : ""}</div></div>
    ${matchHtml}
    ${termsHtml(d)}
    ${assigned ? `<h4>Что изменилось по сравнению с прошлым КП</h4><div class="chips-line">${diffSummaryHtml(offer.diff)}</div>${diffTableHtml(offer.diff, d.currency)}` : ""}
    <details class="how" ${assigned && offer.diff.has_previous ? "" : "open"}><summary>Все позиции (${d.items.length})</summary>${itemsTableHtml(d)}</details>
  </article>`;
}

async function fillPickers() {
  const sel = [...document.querySelectorAll("select[data-pick]")];
  if (!sel.length) return;
  const {items} = await jget("/api/suppliers");
  const opts = items.map(s => `<option value="${esc(s.domain)}">${esc(s.name)} (${esc(s.domain)})</option>`).join("");
  sel.forEach(s => s.insertAdjacentHTML("beforeend", opts));
}

async function sendOffer(formData) {
  setOfferStatus("Распознаю документ… обычно 5–20 секунд");
  $("#offer-send").disabled = true; $("#pick-file").disabled = true;
  try {
    const res = await api("/api/offers", {method: "POST", body: formData}).then(r => r.json());
    setOfferStatus(res.match.assigned ? "Готово: КП распознано и привязано к поставщику." : "Готово: КП распознано, осталось выбрать поставщика.");
    $("#offer-result").innerHTML = offerCardHtml(res); fillPickers();
    $("#offer-text").value = "";
    loadOverview();
    $("#offer-result").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (err) { setOfferStatus(err.message, true); }
  finally { $("#offer-send").disabled = false; $("#pick-file").disabled = false; }
}
function setOfferStatus(text, err = false) { const s = $("#offer-status"); s.textContent = text; s.className = "status" + (err ? " err" : ""); }

function uploadFile(file) {
  if (!file) return;
  const fd = new FormData(); fd.append("file", file); sendOffer(fd);
}
async function assignOffer(offerId, body) {
  try {
    const r = await jpost(`/api/offers/${offerId}/assign`, body);
    const cands = [];
    $("#offer-result").innerHTML = offerCardHtml({offer: r.offer, match: {assigned: r.offer.domain, candidates: cands}});
    setOfferStatus("Готово: КП привязано к поставщику."); loadOverview();
  } catch (err) { setOfferStatus(err.message, true); }
}

function sparkline(series) {
  if (series.length < 2) return "";
  const w = 96, h = 26, pad = 3, ys = series.map(p => p.price), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const pts = series.map((p, i) => `${(pad + i * (w - 2 * pad) / (series.length - 1)).toFixed(1)},${(h - pad - (p.price - lo) / span * (h - 2 * pad)).toFixed(1)}`).join(" ");
  const trend = ys[ys.length - 1] > ys[0] ? "up" : ys[ys.length - 1] < ys[0] ? "down" : "same";
  const last = pts.split(" ").pop().split(",");
  return `<svg class="spark ${trend}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="3"/></svg>`;
}

async function loadOverview() {
  try {
    const [ov, un] = await Promise.all([jget("/api/offers/overview"), jget("/api/offers/unassigned")]);
    const total = ov.items.reduce((a, i) => a + i.count, 0) + un.items.length;
    $("#offers-n").textContent = total ? `(${total})` : "";
    $("#offer-unassigned").innerHTML = un.items.length
      ? `<h3 class="sec">Ждут привязки к поставщику (${un.items.length})</h3><div class="cards">${un.items.map(o => offerCardHtml({offer: o, match: {assigned: null, candidates: o.candidates}})).join("")}</div>` : "";
    fillPickers();
    $("#offer-overview").innerHTML = ov.items.length
      ? `<h3 class="sec">Поставщики с КП</h3><div class="cards">${ov.items.map(i => {
          const s = i.last.diff.summary || {};
          return `<article class="card ov" data-offer-open="${esc(i.domain)}"><div class="ov-row"><div><h3>${esc(i.name)}</h3>
            <div class="dom">${i.count} ${plural(i.count, "КП", "КП", "КП")} · последнее от ${fmtDate(i.last.received_at)}${i.last.demo ? ' · <span class="demo-badge">пример</span>' : ""}</div></div>
            <div class="chips-line">${i.last.diff.has_previous ? diffSummaryHtml(i.last.diff) : '<span class="dom">первое КП</span>'}</div>
            <button class="ghost" data-offer-open="${esc(i.domain)}">История и цены</button></div></article>`; }).join("")}</div>`
      : `<div class="empty">КП пока нет. Загрузите первое или попробуйте пример.</div>`;
  } catch (err) { setOfferStatus(err.message, true); }
}

async function openSupplierOffers(domain) {
  state.offers.detail = domain;
  const box = $("#offer-detail");
  box.innerHTML = `<div class="empty">Загружаю…</div>`;
  try {
    const [r, sup] = await Promise.all([jget(`/api/supplier/${encodeURIComponent(domain)}/offers`), jget("/api/suppliers")]);
    const name = sup.items.find(s => s.domain === domain)?.name || domain;
    if (!r.offers.length) {
      box.innerHTML = `<div class="detail-head"><h3 class="sec" style="margin:0">${esc(name)}: история КП</h3><button class="ghost" id="detail-close">Закрыть</button></div>
        <div class="empty">По этому поставщику КП ещё нет. Загрузите его ответ выше: сервис сам привяжет документ по ИНН, почте или телефону.</div>`;
      box.scrollIntoView({behavior: "smooth", block: "start"}); return;
    }
    const changedFirst = [...r.history].sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0));
    const hist = r.history.length ? `<h4>Динамика цен</h4><div class="tablewrap"><table class="price"><thead><tr><th>Позиция</th><th>График</th><th>Цены по КП</th><th>Итого</th></tr></thead><tbody>${changedFirst.map(h => `<tr>
        <td>${esc(h.name)}<div class="dom">${esc(h.unit)}</div></td><td>${sparkline(h.series)}</td>
        <td class="dom">${h.series.map(p => `${fmtDate(p.date)}: <b>${Number(p.price).toLocaleString("ru-RU")}</b>`).join(" → ")}</td>
        <td>${h.change_pct == null ? "" : h.change_pct === 0 ? `<span class="tr same">без изменений</span>` : `<span class="tr ${h.change_pct > 0 ? "up" : "down"}">${h.change_pct > 0 ? "▲ +" : "▼ −"}${Math.abs(h.change_pct).toLocaleString("ru-RU")}%</span>`}</td></tr>`).join("")}</tbody></table></div>` : "";
    const list = r.offers.map(o => `<article class="card offer-card" data-offer-card="${o.id}">
      <div class="card-head"><div class="score kp">КП</div><div style="flex:1;min-width:0">
        <h3>${esc(o.filename)} ${o.demo ? '<span class="demo-badge">пример, данные вымышлены</span>' : ""}</h3>
        <div class="dom">${o.data.offer_date ? "от " + fmtDate(o.data.offer_date) : "загружено " + fmtDate(o.received_at)}${o.data.valid_until ? " · действует до " + fmtDate(o.data.valid_until) : ""}</div>
        <p class="why">${esc(o.data.summary || "")}</p></div></div>
      ${termsHtml(o.data)}
      <div class="chips-line">${diffSummaryHtml(o.diff)}</div>${diffTableHtml(o.diff, o.data.currency)}
      <details class="how"><summary>Все позиции (${o.data.items.length})</summary>${itemsTableHtml(o.data)}</details>
      <div class="actions">${o.has_file ? `<a class="ghost" href="/api/offers/${o.id}/file" style="text-decoration:none">Скачать оригинал</a>` : ""}
        <button class="ghost" data-offer-delete="${o.id}">Удалить КП</button></div></article>`).join("");
    box.innerHTML = `<div class="detail-head"><h3 class="sec" style="margin:0">${esc(name)}: история КП</h3><button class="ghost" id="detail-close">Закрыть</button></div>${hist}<div class="cards">${list}</div>`;
    box.scrollIntoView({behavior: "smooth", block: "start"});
  } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}

async function loadDemoOffers() {
  setOfferStatus("Загружаю два примера КП…");
  try {
    const r = await jpost("/api/offers/demo", {});
    setOfferStatus("Готово: загружены два вымышленных КП одной компании. Посмотрите, как изменились цены между ними.");
    $("#offer-result").innerHTML = ""; await loadOverview(); openSupplierOffers(r.domain);
  } catch (err) { setOfferStatus(err.message, true); }
}

/* ---------- вкладки ---------- */
function tab(name) {
  state.tab = name;
  $("#view-search").hidden = name !== "search"; $("#view-offers").hidden = name !== "offers"; $("#view-short").hidden = name !== "short";
  $("#search-panel").hidden = name !== "search"; $("#offers-hero").hidden = name !== "offers";
  for (const [id, n] of [["tab-search", "search"], ["tab-offers", "offers"], ["tab-short", "short"]]) $("#" + id).classList.toggle("on", name === n);
  if (name === "short") renderShort();
  if (name === "offers") loadOverview();
  window.scrollTo({top: 0});
}

/* ---------- события ---------- */
$("#form").addEventListener("submit", startSearch);
$("#v-cards").onclick = () => { state.view = "cards"; state.compareOnly = false; $("#v-cards").classList.add("on"); $("#v-table").classList.remove("on"); render(); };
$("#v-table").onclick = () => { state.view = "table"; $("#v-table").classList.add("on"); $("#v-cards").classList.remove("on"); render(); };
$("#quotes").onclick = e => { state.quotes = !state.quotes; e.target.classList.toggle("on", state.quotes); render(); };
$("#hidden-toggle").onclick = () => { state.showHidden = !state.showHidden; render(); };
$("#sort").onchange = e => { state.sort = e.target.value; render(); };
$("#find").oninput = e => { state.find = e.target.value; render(); };
$("#copy-link").onclick = () => copyText(location.href, "Ссылка скопирована");
$("#weights-toggle").onclick = e => { const p = $("#weights-panel"); p.hidden = !p.hidden; e.target.classList.toggle("on", !p.hidden); };
$("#weights-panel").addEventListener("input", onWeightInput);
$("#weights-panel").addEventListener("click", e => {
  if (e.target.id !== "weights-reset") return;
  state.weights = {}; store.set("weights", {}); renderWeights(); loadResults().then(() => { render(); updateHash(); updateExportLinks(); });
});
$("#compare-bar").addEventListener("click", e => {
  const b = e.target.closest("[data-bar]"); if (!b) return;
  if (b.dataset.bar === "compare") { state.compareOnly = true; }
  if (b.dataset.bar === "all") { state.compareOnly = false; }
  if (b.dataset.bar === "clear") { state.selected.clear(); state.compareOnly = false; }
  render();
});
$("#results").addEventListener("click", e => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const domain = b.closest("[data-domain]").dataset.domain;
  if (b.dataset.act === "short") toggleShort(domain);
  if (b.dataset.act === "letter") openLetter(domain).catch(err => toast(err.message));
  if (b.dataset.act === "offers") { tab("offers"); openSupplierOffers(domain); }
  if (b.dataset.act === "refresh") {
    b.textContent = "обновляю…"; b.disabled = true;
    jpost(`/api/supplier/${encodeURIComponent(domain)}/refresh?search_id=${state.searchId}`, {}).then(() => loadResults()).then(() => { render(); toast("Данные обновлены"); })
      .catch(err => { toast(err.message); render(); });
  }
});
$("#results").addEventListener("change", e => {
  if (e.target.dataset.act !== "select") return;
  const domain = e.target.closest("[data-domain]").dataset.domain;
  if (e.target.checked) { if (state.selected.size >= 6) { e.target.checked = false; toast("Для сравнения можно выбрать до 6 поставщиков"); return; } state.selected.add(domain); }
  else state.selected.delete(domain);
  renderCompareBar();
});
$("#recent").addEventListener("click", e => {
  const c = e.target.closest("[data-recent]"); if (!c) return;
  const r = store.get("recent", [])[Number(c.dataset.recent)];
  $("#category").value = r.category; $("#region").value = r.region;
  if (state.health.live_available) $("#demo").checked = r.demo;
  $("#form").requestSubmit();
});
$("#short-list").addEventListener("click", async e => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const domain = b.closest("[data-domain]").dataset.domain;
  if (b.dataset.act === "letter") openLetter(domain).catch(err => toast(err.message));
  if (b.dataset.act === "offers") { tab("offers"); openSupplierOffers(domain); }
  if (b.dataset.act === "refresh") {
    b.textContent = "обновляю…"; b.disabled = true;
    jpost(`/api/supplier/${encodeURIComponent(domain)}/refresh`, {}).then(() => { toast("Данные обновлены"); renderShort(); }).catch(err => { toast(err.message); renderShort(); });
  }
  if (b.dataset.act === "unshort") {
    await jpost(`/api/supplier/${encodeURIComponent(domain)}`, {shortlisted: false}, "PATCH");
    const it = state.results.find(r => r.domain === domain); if (it) it.shortlisted = false; renderShort();
  }
});
$("#short-list").addEventListener("change", e => {
  const el = e.target, domain = el.closest("[data-domain]")?.dataset.domain; if (!domain) return;
  if (el.dataset.act === "status") jpost(`/api/supplier/${encodeURIComponent(domain)}`, {status: el.value}, "PATCH");
  if (el.dataset.act === "note") jpost(`/api/supplier/${encodeURIComponent(domain)}`, {note: el.value}, "PATCH");
});
$("#bulk-open").onclick = () => openBulk().catch(err => toast(err.message));
$("#b-close").onclick = () => $("#bulk-modal").classList.remove("open");
$("#bulk-modal").addEventListener("click", e => { if (e.target.id === "bulk-modal") $("#bulk-modal").classList.remove("open"); });
["b-subj", "b-body"].forEach(id => $("#" + id).addEventListener("input", updateBulkMail));
$("#b-copy-mails").onclick = async () => { await copyText(state.bulk.emails.join(", "), `Адресов скопировано: ${state.bulk.emails.length}`); markBulkOnce(); };
$("#b-copy-text").onclick = async () => { await copyText($("#b-subj").value + "\n\n" + $("#b-body").value, "Письмо скопировано"); markBulkOnce(); };
$("#b-mail").addEventListener("click", markBulkOnce);

["l-to", "l-subj", "l-body"].forEach(id => $("#" + id).addEventListener("input", updateMailto));
$("#l-copy").onclick = async () => { await copyText($("#l-subj").value + "\n\n" + $("#l-body").value); };
$("#l-close").onclick = () => $("#modal").classList.remove("open");
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") $("#modal").classList.remove("open"); });
$("#l-ai").onclick = () => openLetter(state.letterDomain, true).catch(err => toast(err.message));

$("#tab-search").onclick = () => tab("search");
$("#tab-offers").onclick = () => tab("offers");
$("#tab-short").onclick = () => tab("short");

/* КП: загрузка, перетаскивание, вставка текста, выбор поставщика */
$("#pick-file").onclick = () => $("#offer-file").click();
$("#offer-file").onchange = e => { uploadFile(e.target.files[0]); e.target.value = ""; };
$("#offer-drop").addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && e.target.id === "offer-drop") { e.preventDefault(); $("#offer-file").click(); } });
const drop = $("#offer-drop");
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => uploadFile(e.dataTransfer.files[0]));
$("#offer-send").onclick = () => {
  const text = $("#offer-text").value.trim();
  if (!text) { setOfferStatus("Вставьте текст письма или выберите файл", true); return; }
  const fd = new FormData(); fd.append("text", text); sendOffer(fd);
};
$("#offer-demo").onclick = loadDemoOffers;
$("#view-offers").addEventListener("click", async e => {
  const t = e.target;
  const open = t.closest("[data-offer-open]");
  if (open) { openSupplierOffers(open.dataset.offerOpen); return; }
  if (t.id === "detail-close") { $("#offer-detail").innerHTML = ""; return; }
  const del = t.closest("[data-offer-delete]");
  if (del) {
    if (!confirm("Удалить это КП из истории поставщика?")) return;
    await api(`/api/offers/${del.dataset.offerDelete}`, {method: "DELETE"});
    toast("КП удалено"); loadOverview(); if (state.offers.detail) openSupplierOffers(state.offers.detail); return;
  }
  const a = t.closest("[data-assign]"); if (a) { assignOffer(a.dataset.offer, {domain: a.dataset.assign}); return; }
  const ap = t.closest("[data-assign-pick]");
  if (ap) {
    const sel = document.querySelector(`select[data-pick="${ap.dataset.assignPick}"]`);
    if (!sel.value) { toast("Выберите поставщика из списка"); return; }
    assignOffer(ap.dataset.assignPick, {domain: sel.value}); return;
  }
  const c = t.closest("[data-create]"); if (c) assignOffer(c.dataset.create, {create: true});
});

window.addEventListener("hashchange", openFromHash);

/* ---------- старт ---------- */
(async function init() {
  const h = state.health = await jget("/api/health");
  state.weights = store.get("weights", {});
  const demo = $("#demo");
  if (!h.live_available) {
    demo.checked = true; demo.disabled = true;
    $("#mode-note").textContent = "Живой поиск отключён: на сервере не заданы ключи API.";
    $("#offer-note").textContent = "Распознавание своих файлов недоступно без ключей API. Пример работает.";
    $("#offer-send").disabled = true; $("#pick-file").disabled = true;
  } else {
    $("#mode-note").textContent = `Живых поисков сегодня осталось: ${h.live_left_today}`;
    $("#l-ai").hidden = false;
  }
  $("#topics").innerHTML = h.demo_topics.map(t => `<button type="button" class="chip" data-topic="${esc(t.topic)}" data-region="${esc(t.region)}">${esc(t.topic)}</button>`).join(" ");
  $("#topics").onclick = e => { const c = e.target.closest(".chip"); if (!c) return; $("#category").value = c.dataset.topic; $("#region").value = c.dataset.region; };
  renderWeights(); renderRecent();
  if (innerWidth <= 720 || innerHeight <= 480) $("#more").open = false;   // на телефоне фильтры свёрнуты, чтобы форма была видна сразу
  refreshShortCount();
  jget("/api/offers/overview").then(ov => { const n = ov.items.reduce((a, i) => a + i.count, 0) + ov.unassigned; $("#offers-n").textContent = n ? `(${n})` : ""; }).catch(() => {});
  openFromHash();
})();
