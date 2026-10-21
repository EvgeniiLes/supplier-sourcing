"""Прозрачная оценка поставщика 0–100 по правилам: каждая причина видна пользователю."""
import re

from .models import Filters, ScoreResult, Supplier

ALIASES = {"спб": "петербург", "питер": "петербург", "мск": "москва", "екб": "екатеринбург",
           "нск": "новосибирск", "нн": "нижний"}
STOP = {"город", "область", "обл", "край", "республика", "респ", "россия", "санкт", "федерация"}


def region_stems(text: str) -> set[str]:
    words = re.findall(r"[а-яё]+", text.lower().replace("ё", "е"))
    stems = set()
    for w in words:
        w = ALIASES.get(w, w)
        if len(w) > 3 and w not in STOP:
            stems.add(w[:4])
    return stems


def region_matches(user_region: str, supplier_text: str) -> bool:
    a = region_stems(user_region)
    return bool(a) and bool(a & region_stems(supplier_text))


def score_supplier(s: Supplier, region: str, f: Filters) -> ScoreResult:
    pros: list[str] = []
    cons: list[str] = []
    missing: list[str] = []
    fails: list[str] = []
    total = 0

    # регион — 30
    region_known = bool(region.strip())
    if not region_known:
        total += 15
    elif s.address and region_matches(region, s.address.value):
        total += 30
        pros.append(f"находится в вашем регионе ({s.address.value})")
    elif any(region_matches(region, r.value) for r in s.regions):
        total += 26
        pros.append("заявлена работа с вашим регионом")
    elif s.nationwide:
        total += 18
        pros.append("заявлена доставка по России")
    elif s.address or s.regions:
        cons.append("регион работы не совпадает с вашим")
        if f.delivery_to_region:
            fails.append("нет доставки в ваш регион")
    else:
        total += 6
        cons.append("регион работы не указан")
        missing.append("работаете ли вы с нашим регионом")

    # контакты — 20
    if s.phones:
        total += 8
    else:
        cons.append("нет телефона"); missing.append("контактный телефон")
    if s.emails:
        total += 8
    else:
        cons.append("нет email"); missing.append("email для заявок")
    total += 4  # у каждого есть сайт
    if s.phones and s.emails:
        pros.append("есть телефон и email")

    # минимальный заказ — 15
    if s.min_order:
        if f.max_min_order_kg is not None and s.min_order_kg is not None:
            if s.min_order_kg <= f.max_min_order_kg:
                total += 15
                pros.append(f"минималка {s.min_order.value} — в вашем лимите")
            else:
                cons.append(f"минималка {s.min_order.value} выше вашего лимита")
                fails.append("минимальный заказ больше допустимого")
        else:
            total += 12
            pros.append(f"минимальный заказ: {s.min_order.value}")
    else:
        total += 3
        cons.append("минимальный заказ не указан"); missing.append("минимальный объём заказа")

    # документы — 15
    if s.certificates:
        total += 15
        pros.append("есть документы/сертификаты")
    else:
        cons.append("документы и сертификаты на сайте не найдены")
        missing.append("сертификаты/декларации на продукцию")
        if f.need_certs:
            fails.append("нет документов или сертификатов")

    # доставка — 10
    if s.delivery:
        total += 10
        pros.append("описаны условия доставки")
    else:
        cons.append("условия доставки не указаны"); missing.append("условия и стоимость доставки")

    # цены — 10
    if s.price_info:
        total += 10
        pros.append("опубликованы цены")
    else:
        cons.append("цен на сайте нет"); missing.append("прайс-лист / цены")

    return ScoreResult(
        score=max(0, min(100, total)), pros=pros, cons=cons, missing=missing,
        passes=not fails, fail_reasons=fails,
    )
