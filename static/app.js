"use strict";
/* Снабженец-ассистент: интерфейс. Без сборки и зависимостей. */

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const STATUS = {new: "Новый", contacted: "Написал", quote: "Получил КП", agreed: "Договорились", rejected: "Отказ"};

/* один набор линейных иконок (24×24, контур): вместо смеси символов ✓ ✗ + − ↗ */
const ICONS = {
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
  mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
  truck: '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.7a1 1 0 0 0-.2-.6l-3.3-4.2a1 1 0 0 0-.8-.4H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/>',
  pin: '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
  box: '<path d="m7.5 4.3 9 5.2"/><path d="M21 8v8a2 2 0 0 1-1 1.7l-7 4a2 2 0 0 1-2 0l-7-4A2 2 0 0 1 3 16V8a2 2 0 0 1 1-1.7l7-4a2 2 0 0 1 2 0l7 4A2 2 0 0 1 21 8z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
  tag: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r="1" fill="currentColor"/>',
  building: '<path d="M3 21h18M5 21V7l8-4v18M19 21V11l-6-4M9 9v.01M9 12v.01M9 15v.01M9 18v.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  minus: '<path d="M5 12h14"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.7 8.9a1 1 0 0 1-.6 0C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.2-2.7a1.2 1.2 0 0 1 1.6 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10 15 15 0 0 1-4-10 15 15 0 0 1 4-10z"/>',
  star: '<path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  ext: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  mailsend: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
};
const icon = (name, cls = "") => `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const store = {   // localStorage может быть недоступен (приватный режим): страница должна работать и без него
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ничего */ } },
};

const state = {
  health: null, searchId: null, search: null, results: [], view: "cards", quotes: false, showHidden: false,
  selected: new Set(), compareOnly: false, sort: "score", find: "", weights: {}, poll: null,
  ctx: store.get("ctx", {category: "", region: ""}), letterDomain: null, tab: "search", lastSig: "",
  bulk: {emails: [], marked: false}, offers: {detail: null}, open: new Set(), seen: new Set(),
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
/* доказательства (цитата + страница) копятся при отрисовке; кнопка-щит в карточке показывает их во всплывающем окне */
const EV = [];
function evBtn(f) {
  if (!f.url) return `<span class="src from-kp" title="Данные из загруженного КП">из КП</span>`;
  EV.push({value: f.value || "", quote: f.quote || "", url: f.url});
  return `<button type="button" class="ev" data-ev="${EV.length - 1}" aria-label="Подтверждено на сайте: показать цитату">${icon("shield")}</button>`;
}
function fact(f) {
  if (!f) return `<span class="miss">не указано — уточнить</span>`;
  return `<span>${esc(f.value)}</span>${evBtn(f)}${f.quote ? `<div class="quote">«${esc(f.quote)}»</div>` : ""}`;
}
/* сколько полей карточки подтверждено цитатой со страницы */
function evidenceCount(d) {
  let n = 0;
  for (const v of Object.values(d || {})) for (const f of (Array.isArray(v) ? v : [v])) if (f && typeof f === "object" && f.quote && f.url) n++;
  return n;
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
    ${evBtn({value: p.name, quote: p.quote, url: p.url})}
    <div class="quote">«${esc(p.quote)}»</div></div>`).join("");
  return rows + (qp.length > 4 ? `<div class="dom">и ещё ${qp.length - 4}</div>` : "");
}

function missingOf(it) {
  const d = it.data ?? it;
  const qp = it.data ? queryPricesOf(it) : null;
  const m = [];
  const knownHere = it.data && (it.delivery_check?.verdict === "yes" || it.presence_check?.verdict === "yes");
  if (!knownHere && !d.regions?.length && !d.nationwide && !d.address) m.push("работаете ли вы с нашим регионом");
  if (!d.min_order) m.push("минимальный объём заказа");
  if (!d.certificates?.length) m.push("сертификаты/декларации на продукцию");
  if (!d.delivery) m.push("условия и стоимость доставки");
  if (qp === null ? !d.price_info : !qp.length) m.push("прайс-лист / цены");
  return m;
}
const field = (label, html, ic = "") => `<div class="f"><dt>${ic ? icon(ic) : ""}${label}</dt><dd>${html}</dd></div>`;
/* раздел карточки: тёмный заголовок с яркой подписью и поля внутри */
const panel = (title, fields, ic = "") => fields.trim() ? `<section class="panel"><h4 class="ptitle">${ic ? icon(ic) : ""}${title}</h4><dl>${fields}</dl></section>` : "";

/* заголовок блока цен: «Цены на «сыр»» только для конкретного товара; для категории («хорека», «продукты») просто «Цены» */
const CATEGORY_QUERY = /хорек|horeca|ресторан|кафе|общепит|продукт|поставщик|оптом|еда\b/i;
function queryTitle(name) {
  const n = String(name || "").trim();
  return !n || n === "искомый товар" || CATEGORY_QUERY.test(n) || n.split(/\s+/).length > 4 ? "Цены" : `Цены на «${esc(n)}»`;
}

/* вердикты по региону поиска: возит ли и есть ли офис/склад в городе. Каждое «да»/«нет» подкреплено цитатой с сайта */
function verdictLine(label, check, texts) {
  if (!check) return "";
  const v = check.verdict === "yes" || check.verdict === "no" ? check.verdict : "unknown";
  const ic = v === "yes" ? icon("check", "vi") : v === "no" ? icon("x", "vi") : icon("minus", "vi");
  const src = check.url ? evBtn({value: "", quote: check.quote, url: check.url}) : "";
  const quote = check.quote ? `<div class="quote">«${esc(check.quote)}»</div>` : "";
  return `<div class="verdict ${v}">${ic}<span><b>${label}:</b> ${texts[v]}</span>${src}${quote}</div>`;
}
function regionVerdictsHtml(it) {
  const region = it.delivery_check?.region || it.presence_check?.region || "";
  const del = verdictLine("Доставка", it.delivery_check, {yes: "заявлена", no: "не доставляет", unknown: "не указана, уточнить"});
  const pres = verdictLine("Офис или склад", it.presence_check, {yes: "есть в вашем городе", no: "—", unknown: "не указан"});
  return del || pres ? field(`В вашем регионе «${esc(region)}»`, del + pres, "pin") : "";
}

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

/* оценка кольцом: цвет по уровню, при наведении или фокусе — из чего она сложилась */
const RING_C = 2 * Math.PI * 27;
function breakdownRows(sc) {
  if (!sc.breakdown?.length) return "";
  return `<table class="breakdown"><tbody>${sc.breakdown.map(b => {
    const pct = b.max ? Math.round(b.points / b.max * 100) : 0;
    return `<tr><td>${esc(b.label)}</td><td class="bar"><span style="width:${pct}%"></span></td><td class="num">${b.points}<span class="dom">/${b.max}</span></td></tr>`;
  }).join("")}</tbody></table>`;
}
function ringHtml(sc, animate) {
  const cls = sc.score >= 70 ? "hi" : sc.score >= 45 ? "mid" : "lo";
  const off = (RING_C * (1 - Math.max(0, Math.min(100, sc.score)) / 100)).toFixed(1);
  const full = RING_C.toFixed(1);
  return `<div class="ringwrap ${cls}" tabindex="0" aria-label="Оценка ${sc.score} из 100">
    <svg class="ring" viewBox="0 0 64 64" aria-hidden="true"><circle class="ring-track" cx="32" cy="32" r="27"/>
      <circle class="ring-val ${animate ? "anim" : ""}" cx="32" cy="32" r="27" style="stroke-dasharray:${full};stroke-dashoffset:${off};--full:${full}"/></svg>
    <span class="ring-num">${sc.score}</span>
    <div class="ringpop" role="tooltip"><b>Оценка ${sc.score} из 100</b>${breakdownRows(sc)}</div></div>`;
}

/* плитки с главным: цена, минимальный заказ, доставка, контакт */
const clip = (s, n = 46) => { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
function keyFacts(it) {
  const d = it.data, qp = queryPricesOf(it), dc = it.delivery_check, pc = it.presence_check;
  const tile = (ic, label, text, st, full = text) => `<div class="kf kf-${st}"><div class="kf-l">${icon(ic)}${label}</div><div class="kf-v" title="${esc(full)}">${esc(text)}</div></div>`;
  let price;
  if (qp === null) price = d.price_info ? tile("tag", "Цена", clip(d.price_info.value), "ok", d.price_info.value) : tile("tag", "Цена", "нет на сайте, запросить КП", "gap");
  else if (qp.length) price = tile("tag", "Цена", `${bestLabel(it)}${qp.length > 1 ? ` · ${qp.length} поз.` : ""}`, "ok");
  else price = tile("tag", "Цена", "нет на сайте, запросить КП", "gap");
  const minOrder = d.min_order ? tile("box", "Мин. заказ", clip(d.min_order.value), "ok", d.min_order.value) : tile("box", "Мин. заказ", "не указан", "gap");
  let delivery;
  if (dc?.verdict === "yes") delivery = tile("truck", "Доставка", "в ваш город подтверждена", "ok");
  else if (dc?.verdict === "no") delivery = tile("truck", "Доставка", "в ваш город не возит", "bad");
  else if (pc?.verdict === "yes") delivery = tile("building", "В вашем городе", "есть офис или склад", "ok");
  else delivery = d.delivery ? tile("truck", "Доставка", clip(d.delivery.value), "ok", d.delivery.value) : tile("truck", "Доставка", "не указана", "gap");
  const c = d.phones?.[0] || d.emails?.[0];
  const contact = c ? tile(d.phones?.[0] ? "phone" : "mail", "Контакт", c.value, "ok") : tile("phone", "Контакт", "не найден", "gap");
  return `<div class="keyfacts">${price}${minOrder}${delivery}${contact}</div>`;
}

/* плюсы и минусы: «не нашли данных» — нейтральный серый, красный только для реальных минусов; про цены уже сказано плиткой */
const isPriceGap = c => /цен[^.]*нет|нет[^.]*цен/i.test(c);
const isHardNo = c => /не доставляет|не совпадает|выше вашего/i.test(c);
function whyHtml(sc, full) {
  const pros = sc.pros, cons = sc.cons.filter(c => !isPriceGap(c));
  const lim = full ? 99 : 2;
  const row = (kind, t) => `<li class="w ${kind}" title="${esc(t)}">${icon(kind === "p" ? "check" : kind === "x" ? "x" : "minus")}<span>${esc(t)}</span></li>`;
  const rows = pros.slice(0, lim).map(t => row("p", t)).concat(cons.slice(0, lim).map(t => row(isHardNo(t) ? "x" : "g", t)));
  const rest = pros.length + cons.length - rows.length;
  return rows.length ? `<ul class="why">${rows.join("")}</ul>${rest > 0 ? `<div class="dom more-n">и ещё ${rest} в «Подробнее»</div>` : ""}` : "";
}

/* отзывы: короткое резюме ИИ по открытым страницам, каждый пункт с цитатой (щит) */
function reviewLine(r) {
  if (!r?.found) return "";
  return `<div class="rev-line">${icon("chat")}${r.rating ? `<b class="rev-r">★ ${esc(r.rating.value)}</b>` : ""}<span>${esc(clip(r.summary, 170))}</span></div>`;
}
function reviewsPanel(r) {
  const head = `<h4 class="ptitle">${icon("chat")}Отзывы клиентов</h4>`;
  const note = text => `<section class="panel panel-wide">${head}<p class="rev-note">${text}</p></section>`;
  if (!r) return note("Отзывы пока не проверяли. Кнопка «обновить» внизу карточки запустит поиск.");
  if (!r.found) return note("Отзывов об этой компании в открытых источниках не нашли. Отзывы Яндекс Карт мы не читаем: у них нет открытого API.");
  const list = (items, kind) => items.length ? `<ul class="why">${items.map(p => `<li class="w ${kind}">${icon(kind === "p" ? "check" : "minus")}<span>${esc(p.text)}${evBtn({value: "", quote: p.quote, url: p.url})}</span></li>`).join("")}</ul>` : "";
  const host = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  return `<section class="panel panel-wide">${head}<div class="rev">
    ${r.rating ? `<div class="rev-rating"><b>★ ${esc(r.rating.value)}</b>${evBtn(r.rating)}<span class="dom">рейтинг, как указан на странице</span></div>` : ""}
    <p class="rev-sum">${esc(r.summary)}</p>
    ${r.positives.length ? `<div class="rev-h">Хвалят</div>${list(r.positives, "p")}` : ""}
    ${r.negatives.length ? `<div class="rev-h">Жалуются</div>${list(r.negatives, "g")}` : ""}
    <p class="dom rev-src">Резюме составил ИИ по открытым отзывам, каждый пункт подтверждён цитатой. Источники: ${r.pages.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(host(u))}</a>`).join(", ")}${r.checked_at ? ` · проверено ${fmtDate(r.checked_at)}` : ""}</p>
  </div></section>`;
}

function cardHtml(it) {
  const d = it.data, sc = it.score;
  const name = d.name ? esc(d.name.value) : esc(d.domain);
  const inShort = it.shortlisted, sel = state.selected.has(d.domain);
  const open = state.open.has(d.domain) || state.quotes;
  const animate = !state.seen.has(d.domain); state.seen.add(d.domain);
  const link = d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.domain)}</a>` : `<span>${esc(d.domain)}</span>`;
  const ev = evidenceCount(d);
  const body = open ? `<div class="card-body">
    ${whyHtml(sc, true) ? `<h4 class="sub">Почему такая оценка</h4>${whyHtml(sc, true)}` : ""}
    <div class="panels">
      ${panel("Товар и цены",
        field("Что поставляют", fact(d.offering), "box") +
        field(queryTitle(queryNameOf(it)), queryPricesHtml(it), "tag") +
        (queryPricesOf(it) !== null && d.price_info ? field("Общие условия по ценам", fact(d.price_info)) : "") +
        field("Минимальный заказ", fact(d.min_order)) +
        field("Опт / юрлица", fact(d.wholesale)), "box")}
      ${panel("Регион и доставка",
        regionVerdictsHtml(it) +
        field("Регион работы", regionsHtml(d), "pin") +
        field("Доставка", fact(d.delivery), "truck"), "truck")}
      ${panel("Контакты",
        field("Телефон", facts(d.phones), "phone") +
        field("Email", facts(d.emails), "mail") +
        field("Адрес", fact(d.address), "pin"), "phone")}
      ${panel("Документы и компания",
        field("Документы и сертификаты", facts(d.certificates), "file") +
        (d.inn ? field("ИНН", fact(d.inn)) : ""), "file")}
      ${reviewsPanel(d.reviews)}
    </div>
    <details class="how"><summary>Как посчитана оценка</summary>${breakdownHtml(sc)}</details>
    <div class="actions">
      <button class="ghost" data-act="letter">${icon("mailsend")}Письмо с запросом КП</button>
      <button class="ghost" data-act="offers">${icon("file")}КП${it.offers_count ? ` (${it.offers_count})` : ": загрузить"}</button>
      ${freshness(it)}
      <span class="dom">${d.rejected_facts ? `Отброшено непроверенных данных: ${d.rejected_facts}` : ""}</span>
    </div></div>` : "";
  return `<article class="card ${sc.passes ? "" : "dim"} ${open ? "open" : ""}" data-domain="${esc(d.domain)}">
    <div class="head2">
      ${ringHtml(sc, animate)}
      <div class="head-main">
        <h3>${name}</h3>
        <div class="dom">${link}${d.also_domains?.length ? " · также: " + esc(d.also_domains.join(", ")) : ""}</div>
        ${ev ? `<div class="verified">${icon("shield")}${ev} ${plural(ev, "поле подтверждено", "поля подтверждены", "полей подтверждено")} цитатами со страниц</div>` : ""}
      </div>
      <div class="head-tools">
        <button class="star ${inShort ? "on" : ""}" data-act="short" aria-pressed="${inShort}" title="${inShort ? "Убрать из шорт-листа" : "В шорт-лист"}">${icon("star")}</button>
        <label class="pick" title="Выбрать для сравнения"><input type="checkbox" data-act="select" ${sel ? "checked" : ""}><span>сравнить</span></label>
      </div>
    </div>
    ${sc.passes ? "" : `<div class="fail">Не подходит по фильтрам: ${esc(sc.fail_reasons.join("; "))}</div>`}
    ${keyFacts(it)}
    ${reviewLine(d.reviews)}
    ${open ? "" : whyHtml(sc, false)}
    <button class="more-btn" data-act="toggle" aria-expanded="${open}">${open ? "Свернуть" : "Подробнее"}${icon("chevron")}</button>
    ${body}
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
  EV.length = 0;
  box.innerHTML = (state.view === "table" || state.compareOnly) ? tableHtml(shown) : `<div class="cards">${shown.map(cardHtml).join("")}</div>`;
}

/* ---------- поиск ---------- */
function setStatus(text, err = false) { const s = $("#status"); s.textContent = text; s.className = "status" + (err ? " err" : ""); }
function setProgress(done, total) {
  const p = $("#progress"); p.hidden = !(total > 0);
  $("#progress-bar").style.width = total ? `${Math.round(done / total * 100)}%` : "0";
}

/* три условия, можно любые вместе: убрать точно не возящих / доставка подтверждена / представитель в городе.
   «Доставка подтверждена» строже, чем «убрать не возящих», поэтому при обоих действует она */
const rfBox = v => document.querySelector(`input[name="rf"][value="${v}"]`);
function regionFilter() {
  const on = v => rfBox(v).checked;
  return {delivery: on("yes") ? "yes" : on("not_no") ? "not_no" : "any", presence: on("presence")};
}
function setRegionFilter(filters) {
  rfBox("yes").checked = filters.delivery === "yes";
  rfBox("not_no").checked = filters.delivery === "not_no";
  rfBox("presence").checked = !!filters.presence;
}
document.querySelector(".region-filter").addEventListener("change", e => {
  if (e.target.value === "yes" && e.target.checked) rfBox("not_no").checked = false;       // уже включено в «доставка в ваш город»
  if (e.target.value === "not_no" && e.target.checked) rfBox("yes").checked = false;
});
function currentFilters() {
  return {need_certs: $("#f-certs").checked, max_min_order_kg: $("#f-min").value ? Number($("#f-min").value) : null,
          max_min_order_rub: $("#f-min-rub").value ? Number($("#f-min-rub").value) : null, ...regionFilter()};
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
  state.open.clear(); state.seen.clear();
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
    $("#f-certs").checked = !!s.filters.need_certs; setRegionFilter(s.filters);
    $("#f-min").value = s.filters.max_min_order_kg ?? ""; $("#f-min-rub").value = s.filters.max_min_order_rub ?? ""; $("#demo").checked = s.mode !== "live" || !state.health.live_available;
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
      ${(queryPricesOf(it) || []).length ? `<div class="price-line">${queryTitle(queryNameOf(it))}:${queryPricesOf(it).slice(0, 3).map(p => `${esc(p.name)} <b>${esc(priceText(p))}</b>`).join("; ")}</div>` : ""}
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
/* Gmail в браузере: окно нового письма с заполненными полями (для тех, у кого нет почтовой программы) */
/* Mail.ru в браузере: тот же адрес, которым почта регистрируется обработчиком ссылок mailto: */
const mailruLink = mailto => "https://e.mail.ru/compose/?mailto=" + encodeURIComponent(mailto);
const gmailLink = p => "https://mail.google.com/mail/?view=cm&fs=1" + Object.entries(p).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join("");
function updateMailto() {
  $("#l-mail").href = `mailto:${encodeURIComponent($("#l-to").value)}?subject=${encodeURIComponent($("#l-subj").value)}&body=${encodeURIComponent($("#l-body").value)}`;
  $("#l-gmail").href = gmailLink({to: $("#l-to").value, su: $("#l-subj").value, body: $("#l-body").value});
  $("#l-mailru").href = mailruLink($("#l-mail").getAttribute("href"));
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
  $("#b-gmail").href = gmailLink({bcc: state.bulk.emails.join(","), su: $("#b-subj").value, body: $("#b-body").value});
  $("#b-mailru").href = mailruLink($("#b-mail").getAttribute("href"));
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
/* свернуть/развернуть карточку: подменяем только её, список не прыгает */
function toggleCard(domain) {
  if (state.open.has(domain)) state.open.delete(domain); else state.open.add(domain);
  const it = state.results.find(r => r.domain === domain), el = document.querySelector(`.card[data-domain="${CSS.escape(domain)}"]`);
  if (it && el) el.outerHTML = cardHtml(it);
}

/* всплывающая проверка: цитата со страницы, на которой нашли значение */
const evPop = document.createElement("div");
evPop.className = "evpop"; evPop.hidden = true; evPop.setAttribute("role", "tooltip");
document.body.appendChild(evPop);
let evTimer;
function evPopHtml(e) {
  let host = e.url; try { host = new URL(e.url).hostname.replace(/^www\./, ""); } catch { /* оставляем как есть */ }
  const q = esc(e.quote), v = e.value ? esc(e.value) : "", i = v ? q.toLowerCase().indexOf(v.toLowerCase()) : -1;
  const marked = i >= 0 ? `${q.slice(0, i)}<mark>${q.slice(i, i + v.length)}</mark>${q.slice(i + v.length)}` : q;
  return `<div class="evh">${icon("shield")}Подтверждено на сайте</div>
    <div class="evd">${icon("globe")}${esc(host)}</div>
    ${e.quote ? `<blockquote>«${marked}»</blockquote>` : ""}
    <a href="${esc(e.url)}" target="_blank" rel="noopener">Открыть страницу${icon("ext")}</a>`;
}
function showEv(btn) {
  clearTimeout(evTimer);
  const e = EV[Number(btn.dataset.ev)]; if (!e) return;
  evPop.innerHTML = evPopHtml(e); evPop.hidden = false; evPop.dataset.for = btn.dataset.ev;
  const w = Math.min(360, innerWidth - 24); evPop.style.width = w + "px";
  const r = btn.getBoundingClientRect(), h = evPop.offsetHeight;
  evPop.style.left = Math.max(12, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 12)) + "px";
  evPop.style.top = (r.bottom + 10 + h > innerHeight && r.top > h + 20 ? r.top - h - 10 : r.bottom + 10) + "px";
}
const hideEv = (delay = 250) => { clearTimeout(evTimer); evTimer = setTimeout(() => { evPop.hidden = true; }, delay); };
document.addEventListener("mouseover", e => { const b = e.target.closest?.(".ev"); if (b) showEv(b); else if (e.target.closest?.(".evpop")) clearTimeout(evTimer); });
document.addEventListener("mouseout", e => { if (e.target.closest?.(".ev, .evpop")) hideEv(); });
document.addEventListener("focusin", e => { const b = e.target.closest?.(".ev"); if (b) showEv(b); });
document.addEventListener("focusout", e => { if (e.target.closest?.(".ev")) hideEv(); });
document.addEventListener("click", e => { const b = e.target.closest?.(".ev"); if (b) { showEv(b); return; } if (!e.target.closest?.(".evpop")) hideEv(0); });
document.addEventListener("keydown", e => { if (e.key === "Escape") hideEv(0); });
window.addEventListener("scroll", () => hideEv(0), {passive: true});

$("#results").addEventListener("click", e => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const domain = b.closest("[data-domain]").dataset.domain;
  if (b.dataset.act === "toggle") { toggleCard(domain); return; }
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
$("#b-gmail").addEventListener("click", markBulkOnce);
$("#b-mailru").addEventListener("click", markBulkOnce);

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
    const names = {yandex: "Яндекс", tavily: "Tavily"};
    $("#mode-note").textContent = `Источники поиска: ${(h.sources || []).map(s => names[s] || s).join(" + ") || "нет"}. ` +
      `Живых поисков сегодня осталось: ${h.live_left_today}`;
    $("#l-ai").hidden = false;
    demo.checked = !(h.live_left_today > 0);     // живой поиск по умолчанию; демо только если дневной лимит исчерпан
  }
  $("#topics").innerHTML = h.demo_topics.map(t => `<button type="button" class="chip" data-topic="${esc(t.topic)}" data-region="${esc(t.region)}">${esc(t.topic)}</button>`).join(" ");
  $("#topics").onclick = e => { const c = e.target.closest(".chip"); if (!c) return; $("#category").value = c.dataset.topic; $("#region").value = c.dataset.region; };
  renderWeights(); renderRecent();
  if (innerWidth <= 720 || innerHeight <= 480) $("#more").open = false;   // на телефоне фильтры свёрнуты, чтобы форма была видна сразу
  refreshShortCount();
  jget("/api/offers/overview").then(ov => { const n = ov.items.reduce((a, i) => a + i.count, 0) + ov.unassigned; $("#offers-n").textContent = n ? `(${n})` : ""; }).catch(() => {});
  openFromHash();
})();
