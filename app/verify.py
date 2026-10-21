"""Проверки без LLM: каждое значение должно подтверждаться текстом страницы."""
import re

from .models import Fact, Supplier

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
