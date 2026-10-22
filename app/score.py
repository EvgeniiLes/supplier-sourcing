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


def brief(text: str, limit: int = 60) -> str:
    """Короткая версия значения для строки с причинами оценки; полный текст остаётся в карточке."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,;(") + "…"


# критерий, подпись, вес по умолчанию. Сумма весов 20: при вес*5 получаются привычные 30/20/15/15/10/10 баллов
CRITERIA = (
    ("region", "Регион", 6),
    ("contacts", "Контакты", 4),
    ("min_order", "Минимальный заказ", 3),
    ("docs", "Документы и сертификаты", 3),
    ("delivery", "Условия доставки", 2),
    ("price", "Цены на искомый товар", 2),
)
DEFAULT_WEIGHTS = {key: w for key, _, w in CRITERIA}


def normalize_weights(weights: dict | None) -> dict[str, float]:
    """Веса от пользователя 0..10; отсутствующие берём по умолчанию, все нули — тоже по умолчанию."""
    w: dict[str, float] = dict(DEFAULT_WEIGHTS)
    for key, value in (weights or {}).items():
        if key in w:
            try:
                w[key] = min(max(float(value), 0.0), 10.0)
            except (TypeError, ValueError):
                pass
    return w if sum(w.values()) > 0 else dict(DEFAULT_WEIGHTS)


def score_supplier(s: Supplier, region: str, f: Filters, weights: dict | None = None,
                   has_query_prices: bool | None = None, delivery_check: dict | None = None,
                   presence_check: dict | None = None) -> ScoreResult:
    """has_query_prices: есть ли на сайте цены именно на искомый товар. None — извлечение не знало запроса
    (старые данные), тогда годятся любые опубликованные цены.
    delivery_check: вердикт «возит ли в ваш регион» по тексту сайта для этого поиска ({verdict, quote, url})."""
    pros: list[str] = []
    cons: list[str] = []
    missing: list[str] = []
    fails: list[str] = []
    frac: dict[str, float] = {}
    note: dict[str, str] = {}

    # регион и доставка в него
    region_given = bool(region.strip())
    dc = delivery_check
    if dc is None and s.delivery_check and region_stems(s.delivery_check.region) == region_stems(region):
        dc = s.delivery_check.model_dump()        # запасной вариант: вердикт у поставщика, но только если он для этого же региона
    verdict = (dc or {}).get("verdict") if region_given else None          # yes | no | unknown | None (не проверяли)
    mode = f.delivery                                                      # any | not_no | yes
    pc = presence_check
    if pc is None and s.presence_check and region_stems(s.presence_check.region) == region_stems(region):
        pc = s.presence_check.model_dump()
    addr_match = region_given and bool(s.address and region_matches(region, s.address.value))
    reg_match = region_given and any(region_matches(region, r.value) for r in s.regions)
    present = region_given and (addr_match or (pc or {}).get("verdict") == "yes")      # офис, склад или точка в городе
    confirmed = verdict == "yes" or addr_match or reg_match or (region_given and bool(s.nationwide)) or present

    if not region_given:
        frac["region"], note["region"] = 0.5, "регион в запросе не задан"
    elif present:
        frac["region"], note["region"] = 1.0, "есть офис, склад или точка в вашем городе"
        if addr_match:
            pros.append(f"находится в вашем регионе ({brief(s.address.value)})")
        else:
            pros.append(f"есть присутствие в вашем городе: «{brief((pc or {}).get('quote', ''), 80)}»")
    elif verdict == "yes":
        frac["region"], note["region"] = 26 / 30, "доставка в ваш регион подтверждена"
        pros.append("доставка в ваш регион подтверждена на сайте")
    elif verdict == "no":
        frac["region"], note["region"] = 0.0, "сайт: в ваш регион не доставляет"
        cons.append(f"не доставляет в ваш регион: «{brief((dc or {}).get('quote', ''), 90)}»")
        if mode != "any":
            fails.append("не доставляет в ваш регион")
    elif reg_match:
        frac["region"], note["region"] = 26 / 30, "заявлена работа с вашим регионом"
        pros.append("заявлена работа с вашим регионом")
    elif s.nationwide:
        frac["region"], note["region"] = 18 / 30, "заявлена доставка по России"
        pros.append("заявлена доставка по России")
    elif s.address or s.regions:
        frac["region"], note["region"] = 0.0, "регион не совпадает"
        cons.append("регион работы не совпадает с вашим")
        if mode != "any" and dc is None:          # старые данные: вердикта нет, судим по адресу
            fails.append("нет доставки в ваш регион")
    else:
        frac["region"], note["region"] = 6 / 30, "регион не указан"
        cons.append("регион работы не указан")
    if region_given and not confirmed and verdict != "no":
        missing.append("работаете ли вы с нашим регионом")
    if region_given and mode == "yes" and not confirmed and verdict != "no":
        fails.append("доставка в ваш регион не подтверждена")
    if region_given and f.presence and not present:        # отдельный фильтр: нужен офис, склад или точка именно в городе
        fails.append("нет офиса, склада или точки в вашем городе (не подтверждено)")

    # контакты: телефон 8, email 8, сайт 4 (из 20)
    c = 4.0
    if s.phones:
        c += 8
    else:
        cons.append("нет телефона"); missing.append("контактный телефон")
    if s.emails:
        c += 8
    else:
        cons.append("нет email"); missing.append("email для заявок")
    frac["contacts"] = c / 20
    note["contacts"] = ("телефон и email" if s.phones and s.emails
                        else "телефон" if s.phones else "email" if s.emails else "только сайт")
    if s.phones and s.emails:
        pros.append("есть телефон и email")

    # минимальный заказ
    if s.min_order:
        if f.max_min_order_kg is not None and s.min_order_kg is not None:
            if s.min_order_kg <= f.max_min_order_kg:
                frac["min_order"], note["min_order"] = 1.0, "в вашем лимите"
                pros.append(f"минималка {brief(s.min_order.value)} — в вашем лимите")
            else:
                frac["min_order"], note["min_order"] = 0.0, "выше вашего лимита"
                cons.append(f"минималка {brief(s.min_order.value)} выше вашего лимита")
                fails.append("минимальный заказ больше допустимого")
        else:
            frac["min_order"], note["min_order"] = 0.8, "указан"
            pros.append(f"минимальный заказ: {brief(s.min_order.value)}")
    else:
        frac["min_order"], note["min_order"] = 0.2, "не указан"
        cons.append("минимальный заказ не указан"); missing.append("минимальный объём заказа")

    # документы
    if s.certificates:
        frac["docs"], note["docs"] = 1.0, "есть"
        pros.append("есть документы/сертификаты")
    else:
        frac["docs"], note["docs"] = 0.0, "не найдены"
        cons.append("документы и сертификаты на сайте не найдены")
        missing.append("сертификаты/декларации на продукцию")
        if f.need_certs:
            fails.append("нет документов или сертификатов")

    # доставка
    if s.delivery:
        frac["delivery"], note["delivery"] = 1.0, "описана"
        pros.append("описаны условия доставки")
    else:
        frac["delivery"], note["delivery"] = 0.0, "не указана"
        cons.append("условия доставки не указаны"); missing.append("условия и стоимость доставки")

    # цены
    if has_query_prices:
        frac["price"], note["price"] = 1.0, "есть на искомый товар"
        pros.append("опубликованы цены на искомый товар")
    elif has_query_prices is None and s.price_info:        # старые данные: запрос не учитывался
        frac["price"], note["price"] = 1.0, "опубликованы"
        pros.append("опубликованы цены")
    elif has_query_prices is False and s.price_info:       # искали: цены где-то есть, но не на этот товар
        frac["price"], note["price"] = 0.3, "есть, но не на искомый товар"
        cons.append("цен на искомый товар на сайте нет (есть прайс или цены на другое)")
        missing.append("прайс-лист / цены")
    else:
        frac["price"], note["price"] = 0.0, "не опубликованы"
        cons.append("цен на искомый товар на сайте нет" if has_query_prices is False else "цен на сайте нет")
        missing.append("прайс-лист / цены")

    w = normalize_weights(weights)
    total_w = sum(w.values())
    breakdown = []
    total = 0.0
    for key, label, _ in CRITERIA:
        share = w[key] / total_w * 100       # сколько баллов из 100 весит критерий
        points = share * frac[key]
        total += points
        breakdown.append({"key": key, "label": label, "max": round(share, 1),
                          "points": round(points, 1), "note": note[key]})

    return ScoreResult(
        score=max(0, min(100, round(total))), pros=pros, cons=cons, missing=missing,
        passes=not fails, fail_reasons=fails, breakdown=breakdown,
    )
