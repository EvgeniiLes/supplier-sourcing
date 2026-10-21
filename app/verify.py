"""Проверки без LLM: каждое значение должно подтверждаться текстом страницы."""
import re

from .models import Fact, PriceItem, Supplier
from .pricing import UNIT_ALIASES, norm_unit

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
INN_RE = re.compile(r"ИНН\s*[:№]?\s*(\d{10}|\d{12})\b")


def norm(text: str) -> str:
    t = text.lower().replace(" ", " ").replace("ё", "е")
    t = re.sub(r"[«»\"'“”„]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def quote_in_text(quote: str, text_norm: str) -> bool:
    q = norm(quote).strip(" .…")
    q = q.replace("...", " ")
    parts = [p.strip() for p in re.split(r"…|\.\.\.", q) if p.strip()]
    parts = parts or [q]
    return len(q) >= 3 and all(p in text_norm for p in parts)


def normalize_phone(raw: str) -> str | None:
    """+7 (xxx) xxx-xx-xx или None, если номер не российский/не по формату."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "3489":
        return None
    return f"+7 ({digits[:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:]}"


def valid_email(raw: str) -> bool:
    return bool(EMAIL_RE.fullmatch(raw.strip()))


def numbers_in(text: str) -> list[float]:
    """Все числа из текста: и с разделителем тысяч ('1 850,50'), и по отдельности."""
    out: list[float] = []
    for m in re.finditer(r"\d+(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?", text):
        raw = re.sub(r"[  ]", "", m.group(0)).replace(",", ".")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    for m in re.finditer(r"\d+(?:[.,]\d+)?", text):          # группы отдельно: '25 1850' не склеиваем
        try:
            out.append(float(m.group(0).replace(",", ".")))
        except ValueError:
            continue
    return out


def has_number(price: float, numbers: list[float]) -> bool:
    return any(abs(price - n) < 0.006 for n in numbers)


QUERY_STOP = {"для", "оптом", "опт", "купить", "цена", "цены", "заказать", "доставка", "от", "производитель", "поставщик"}


def query_stems(query: str) -> set[str]:
    """Основы слов запроса: «сыр моцарелла» -> {сыр, моцар}. Нужны, чтобы отсеять чужие товары."""
    words = re.findall(r"[a-zа-я0-9]+", norm(query))
    return {w[:5] for w in words if len(w) >= 3 and w not in QUERY_STOP}


def relevant_to_query(name: str, quote: str, query: str) -> bool:
    """Позиция относится к искомому товару: в названии есть основа хотя бы одного слова запроса."""
    stems = query_stems(query)
    if not stems:
        return True
    hay = norm(name)
    return any(st in hay for st in stems)


MAX_QUERY_PRICES = 8
PRICE_RE = re.compile(r"\d+(?:[  ]\d{3})*(?:[.,]\d+)?")
AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|гр|г|т|мл|л)(?![а-яa-z])")
RANGE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*[-–—]\s*\d+(?:[.,]\d+)?\s*(?:кг|гр|г|т|мл|л)(?![а-яa-z])")
TO_BASE = {"кг": ("кг", 1.0), "г": ("кг", 0.001), "гр": ("кг", 0.001), "т": ("кг", 1000.0),
           "л": ("л", 1.0), "мл": ("л", 0.001)}


def _unit_alts(unit: str) -> str:
    u = norm_unit(unit)
    aliases = {u} | {k for k, v in UNIT_ALIASES.items() if v == u}
    return "|".join(sorted((re.escape(a) for a in aliases), key=len, reverse=True))


def unit_next_to_price(quote: str, price: float, unit: str) -> bool:
    """Единица цены действительно написана рядом с ценой: «816,38 ₽/кг», «430 руб за шт», «за 1 кг: 816».
    «Вес 1 кг … Цена от 793 ₽» единицей цены не считается (это вес товара, а не за что цена)."""
    if not norm_unit(unit):
        return False
    alts = _unit_alts(unit)
    for m in PRICE_RE.finditer(quote):
        try:
            if abs(float(re.sub(r"[  ]", "", m.group(0)).replace(",", ".")) - price) > 0.006:
                continue
        except ValueError:
            continue
        after = norm(quote[m.end(): m.end() + 22])
        before = norm(quote[max(0, m.start() - 24): m.start()])
        # между числом и единицей допустимы сноска («21*»), знак рубля и «руб.»
        if re.match(rf"[\s*†‡]*(?:₽|руб\w*\.?|р\.?)?\s*(?:/|за\s*)\s*(?:1\s*)?(?:{alts})(?![а-яa-z])", after):
            return True
        if re.search(rf"за\s*(?:1\s*)?(?:{alts})(?![а-яa-z])\W*(?:цена\W*)?$", before):
            return True
    return False


def page_declares_unit(page_norm: str, unit: str) -> bool:
    """Единица цены объявлена в заголовке прайса или таблицы: «Цена за кг», «Цена, ₽/кг», «руб/кг».
    Нужна для таблиц, где в строке только число. Если на странице такого нет, единице не верим."""
    if not norm_unit(unit):
        return False
    alts = _unit_alts(unit)
    return bool(re.search(rf"цен[а-я]*[^\n]{{0,25}}(?:/|за\s*)\s*(?:1\s*)?(?:{alts})(?![а-яa-z])", page_norm))


# посуда и упаковка продаются поштучно: объём из названия («500 мл») это объём изделия, а не единица цены
NOT_BY_WEIGHT = re.compile(r"контейнер|пакет|стакан|лоток|коробк|короб\b|крышк|тарелк|вилк|ложк|салфетк|плёнк|пленк|фольг|"
                           r"ёмкост|емкост|посуд|трубочк|набор для|подставк|этикетк|скотч|перчатк|упаковка для|бумага")


def single_amount(text: str) -> tuple[float, str] | None:
    """Вес или объём из «25кг», «0.100 кг», «(1л)» -> (количество, 'кг'|'л'). Диапазон («5-6 кг») и несколько значений не берём."""
    text = norm(text)
    if not text or RANGE_RE.search(text):
        return None
    found = AMOUNT_RE.findall(text)
    if len(found) != 1:
        return None
    base, k = TO_BASE[found[0][1]]
    amount = float(found[0][0].replace(",", ".")) * k
    return (amount, base) if amount > 0 else None


def base_price_of(price: float, unit: str, pack: str, name: str) -> tuple[float | None, str, bool]:
    """Цена за кг/л для сравнения: (значение, 'кг'|'л', рассчитано ли).
    Считаем только если цена ЯВНО за штуку/упаковку и известен вес или объём; цену без единицы не трогаем:
    нельзя знать, за что она (за кг или за мешок), и делить наугад значит выдумывать."""
    u = norm_unit(unit)
    if u in ("кг", "л"):
        return price, u, False
    if u in ("шт", "уп", "упак") and not NOT_BY_WEIGHT.search(norm(name)):
        got = single_amount(pack) or single_amount(name)
        if got:
            return round(price / got[0], 2), got[1], True
    return None, "", False


def verify_price_items(items: list[dict], pages: dict[str, str], query: str) -> tuple[list[PriceItem], int]:
    """Цены на искомый товар от модели -> только подтверждённые текстом и относящиеся к запросу.
    Проверяется то, что проверяется точно: цитата, цена в цитате, единица рядом с ценой. «Относится ли товар к запросу»
    решает модель (matches_query); основы слов запроса работают лишь если флага нет. Возвращает (принятые, отброшено)."""
    idx = PageIndex(pages)
    out: list[PriceItem] = []
    seen: set[tuple[str, float]] = set()
    rejected = 0
    for it in items or []:
        try:
            name = str(it["name"]).strip()
            price = float(it["price"])
            quote = str(it.get("quote") or "")
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        url = idx.locate(quote, str(it.get("url") or "")) if quote else None
        flag = it.get("matches_query")
        on_topic = relevant_to_query(name, quote, query) if flag is None else bool(flag)
        key = (norm(name), price)
        if (not name or price <= 0 or url is None or not has_number(price, numbers_in(quote))
                or not on_topic or key in seen):
            rejected += 1
            continue
        seen.add(key)
        unit = str(it.get("unit") or "").strip()
        if unit and not (unit_next_to_price(quote, price, unit) or page_declares_unit(idx.norm.get(url, ""), unit)):
            unit = ""                                  # единица выведена моделью, а в тексте её нет
        unit = norm_unit(unit)                         # «₽/кг», «за шт», «руб/л» -> «кг», «шт», «л»
        pack = str(it.get("pack") or "").strip()
        base, base_unit, derived = base_price_of(price, unit, pack, name)
        out.append(PriceItem(name=name, price=price, unit=unit, pack=pack, quote=quote.strip(), url=url,
                             base_price=base, base_unit=base_unit, derived=derived))
    return out[:MAX_QUERY_PRICES], rejected


class PageIndex:
    """Нормализованные тексты страниц для быстрой проверки цитат."""

    def __init__(self, pages: dict[str, str]):
        self.norm = {u: norm(t) for u, t in pages.items()}
        self.digits = "".join(re.sub(r"\D", "", t) + "|" for t in pages.values())
        self.joined = " ".join(self.norm.values())

    def locate(self, quote: str, url: str) -> str | None:
        """URL страницы, где цитата найдена (сначала указанная моделью)."""
        if url in self.norm and quote_in_text(quote, self.norm[url]):
            return url
        for u, t in self.norm.items():
            if quote_in_text(quote, t):
                return u
        return None


def _check(fact: Fact | None, idx: PageIndex) -> Fact | None:
    if fact is None or not fact.value.strip():
        return None
    url = idx.locate(fact.quote, fact.url)
    if not url:
        return None
    return Fact(value=fact.value.strip(), quote=fact.quote.strip(), url=url)


def verify_supplier(s: Supplier, pages: dict[str, str]) -> Supplier:
    """Оставляет только то, что подтверждено текстом. Подсчитывает отброшенное."""
    idx = PageIndex(pages)
    rejected = 0

    def one(f: Fact | None) -> Fact | None:
        nonlocal rejected
        out = _check(f, idx)
        if f is not None and out is None:
            rejected += 1
        return out

    def many(items: list[Fact]) -> list[Fact]:
        return [x for x in (one(f) for f in items) if x]

    s.name = one(s.name)
    s.offering = one(s.offering)
    s.address = one(s.address)
    s.nationwide = one(s.nationwide)
    s.min_order = one(s.min_order)
    s.price_info = one(s.price_info)
    s.delivery = one(s.delivery)
    s.wholesale = one(s.wholesale)
    s.regions = many(s.regions)
    s.certificates = many(s.certificates)
    if s.min_order is None:
        s.min_order_kg = None

    # телефоны: формат + цифры реально есть на страницах
    phones, seen = [], set()
    for f in s.phones:
        p = normalize_phone(f.value)
        if p and re.sub(r"\D", "", p)[1:] in idx.digits and p not in seen:
            seen.add(p)
            url = idx.locate(f.quote, f.url) or f.url
            phones.append(Fact(value=p, quote=f.quote, url=url))
        else:
            rejected += 1
    s.phones = phones

    emails, seen_e = [], set()
    for f in s.emails:
        e = f.value.strip().lower()
        if valid_email(e) and e in idx.joined and e not in seen_e:
            seen_e.add(e)
            emails.append(Fact(value=e, quote=f.quote, url=idx.locate(f.quote, f.url) or f.url))
        else:
            rejected += 1
    s.emails = emails

    s.inn = regex_inn(pages)
    s.rejected_facts = rejected
    s.sources = list(pages)
    return s


def regex_inn(pages: dict[str, str]) -> Fact | None:
    for url, text in pages.items():
        m = INN_RE.search(text)
        if m:
            return Fact(value=m.group(1), quote=m.group(0), url=url)
    return None


def regex_contacts(pages: dict[str, str]) -> tuple[list[Fact], list[Fact]]:
    """Запасной вариант: телефоны и почта, найденные регулярками, если модель их пропустила."""
    phones, emails = [], []
    seen_p, seen_e = set(), set()
    for url, text in pages.items():
        for m in PHONE_RE.finditer(text):
            p = normalize_phone(m.group(0))
            if p and p not in seen_p and len(phones) < 3:
                seen_p.add(p)
                phones.append(Fact(value=p, quote=m.group(0), url=url))
        for m in EMAIL_RE.finditer(text):
            e = m.group(0).lower()
            if e not in seen_e and len(emails) < 3 and not e.endswith((".png", ".jpg", ".svg", ".webp")):
                seen_e.add(e)
                emails.append(Fact(value=e, quote=m.group(0), url=url))
    return phones, emails


# --- склейка дублей ---------------------------------------------------------

def _keys(s: Supplier) -> set[str]:
    keys = {"p:" + re.sub(r"\D", "", f.value) for f in s.phones}
    if s.inn:
        keys.add(f"i:{s.inn.value}")
    return keys


def dedupe(suppliers: list[Supplier]) -> list[Supplier]:
    """Объединяет карточки с общим ИНН или телефоном (разные домены одной компании)."""
    groups: list[list[Supplier]] = []
    for s in suppliers:
        keys = _keys(s)
        for g in groups:
            if any(keys & _keys(m) for m in g):
                g.append(s)
                break
        else:
            groups.append([s])
    out = []
    for g in groups:
        best = max(g, key=lambda x: x.fact_count())
        best.also_domains = [m.domain for m in g if m is not best]
        out.append(best)
    return out
