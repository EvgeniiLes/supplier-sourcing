import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import reviews
from app.models import Fact, ReviewSummary, Supplier
from app.reviews import (attach_reviews, brand_of, brand_tokens, build_summary, collect_pages, is_fresh, is_review_url)

PAGE_A = "https://reviews.yandex.ru/shop/prodservice.shop"
PAGE_B = "https://2gis.ru/ekaterinburg/firm/1"
TEXT_A = ("Продсервис. Рейтинг магазина 4,6 из 5. Отличные цены, большой выбор. Курьеры всегда вежливые. "
          "Из минусов: склад не всегда своевременно собирает заказы. " + "Отзыв. " * 60)


def sup(name="ТД «Продсервис»", domain="prodservice.ru"):
    return Supplier(domain=domain, url=f"https://{domain}/", name=Fact(value=name, quote=name, url=f"https://{domain}/"))


def tool_input(**kw):
    base = {"found": True, "summary": "Хвалят цены и курьеров, жалуются на сборку заказов.",
            "positives": [{"text": "Хорошие цены", "quote": "Отличные цены, большой выбор", "url": PAGE_A}],
            "negatives": [{"text": "Сборка заказов", "quote": "склад не всегда своевременно собирает заказы", "url": PAGE_A}],
            "rating": {"value": "4,6 из 5", "quote": "Рейтинг магазина 4,6 из 5", "url": PAGE_A}}
    base.update(kw)
    return base


def test_brand_and_tokens_skip_generic_words():
    assert brand_of(sup()) == "Продсервис"
    assert brand_of(sup("Продсервис (ТД Продсервис)")) == "Продсервис (ТД Продсервис)"
    assert brand_of(Supplier(domain="a.ru", url="https://a.ru")) == "a.ru"
    t = brand_tokens(sup("СЕЛЛ-Сервис Хорека", "sell-service.ru"))
    assert "селл" in t and "sell-service" in t
    assert "хорека" not in t and "сервис" not in t              # общие слова: по ним легко спутать с чужой компанией


def test_only_review_pages_of_other_sites_are_taken():
    assert is_review_url("https://otzovik.com/reviews/x/", "prodservice.ru")
    assert is_review_url("https://2gis.ru/ekaterinburg/firm/1/tab/reviews", "prodservice.ru")
    assert is_review_url("https://some-forum.ru/otzyvy-o-prodservice", "prodservice.ru")
    assert not is_review_url("https://prodservice.ru/otzyvy", "prodservice.ru")        # свои отзывы на сайте — реклама
    assert not is_review_url("https://news.ru/article", "prodservice.ru")


def test_summary_keeps_only_quotes_found_on_the_page():
    s = build_summary(tool_input(), {PAGE_A: TEXT_A})
    assert s.found and len(s.positives) == 1 and len(s.negatives) == 1
    assert s.rating and s.rating.value == "4,6 из 5" and s.positives[0].url == PAGE_A
    bad = tool_input(positives=[{"text": "Выдумка", "quote": "Лучшая компания в мире", "url": PAGE_A}])
    s = build_summary(bad, {PAGE_A: TEXT_A})
    assert not s.positives and s.negatives and s.found                                # плохая цитата отброшена, остальное осталось


def test_nothing_verified_means_not_found():
    data = tool_input(positives=[{"text": "x", "quote": "нет такого в тексте", "url": PAGE_A}],
                      negatives=[], rating={"value": "5", "quote": "рейтинг пять", "url": PAGE_A})
    s = build_summary(data, {PAGE_A: TEXT_A})
    assert not s.found and not s.summary and not s.positives                          # резюме без подтверждений не показываем
    assert not build_summary(tool_input(found=False), {PAGE_A: TEXT_A}).found


def test_rating_must_be_in_its_quote():
    data = tool_input(rating={"value": "4,9", "quote": "Рейтинг магазина 4,6 из 5", "url": PAGE_A})
    assert build_summary(data, {PAGE_A: TEXT_A}).rating is None


def run_collect(monkeypatch, urls, pages, region="Екатеринбург", s=None):
    async def fake_urls(sp, rg, http):
        return urls

    async def fake_load(u, client, ttl):
        return (pages[u], []) if u in pages else None

    monkeypatch.setattr(reviews, "search_review_urls", fake_urls)
    monkeypatch.setattr(reviews, "load_page", fake_load)
    return asyncio.run(collect_pages(s or sup(), region, None))


def test_pages_about_another_company_or_city_are_dropped(monkeypatch):
    other = "https://otzovik.com/reviews/other/"
    novosib = "https://2gis.ru/novosibirsk/firm/9"
    pages = {PAGE_A: TEXT_A, other: "Отзывы о совсем другой компании. " * 30,
             novosib: "Продсервис Новосибирск, ул. Ленина. Отзывы клиентов. " * 20,
             PAGE_B: "Продсервис, Екатеринбург, ул. Краснодарская, 11. Отзывы клиентов. " * 20}
    got = run_collect(monkeypatch, [PAGE_A, other, novosib, PAGE_B], pages)
    assert set(got) == {PAGE_A, PAGE_B}              # чужая компания и филиал в другом городе не попали
    assert run_collect(monkeypatch, [PAGE_A], {PAGE_A: "коротко"}) == {}                 # пустая или закрытая страница


def test_search_failure_is_not_remembered_as_no_reviews(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    s = sup()

    async def none_urls(sp, rg, http):
        return None

    monkeypatch.setattr(reviews, "search_review_urls", none_urls)
    assert asyncio.run(attach_reviews(s, "Екатеринбург")) is False and s.reviews is None


class FakeClaude:
    calls = 0

    class messages:
        @staticmethod
        async def create(**kw):
            FakeClaude.calls += 1
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=tool_input())])


def test_attach_reviews_end_to_end_and_freshness(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    s = sup()

    async def pages(sp, rg, http):
        return {PAGE_A: TEXT_A}

    monkeypatch.setattr(reviews, "collect_pages", pages)
    FakeClaude.calls = 0
    assert asyncio.run(attach_reviews(s, "Екатеринбург", client=FakeClaude)) is True
    assert s.reviews.found and s.reviews.pages == [PAGE_A] and FakeClaude.calls == 1
    assert asyncio.run(attach_reviews(s, "Екатеринбург", client=FakeClaude)) is False   # свежие не перечитываем
    assert FakeClaude.calls == 1
    assert asyncio.run(attach_reviews(s, "Екатеринбург", force=True, client=FakeClaude)) is True
    assert FakeClaude.calls == 2


def test_no_review_pages_gives_an_honest_not_found(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    s = sup()

    async def empty(sp, rg, http):
        return {}

    monkeypatch.setattr(reviews, "collect_pages", empty)
    FakeClaude.calls = 0
    assert asyncio.run(attach_reviews(s, "Екатеринбург", client=FakeClaude)) is True
    assert s.reviews.found is False and FakeClaude.calls == 0                           # модель не зовём: читать нечего


def test_freshness_window():
    now = datetime.now(timezone.utc)
    assert is_fresh(ReviewSummary(checked_at=now.isoformat()))
    assert not is_fresh(ReviewSummary(checked_at=(now - timedelta(days=30)).isoformat()))
    assert not is_fresh(None) and not is_fresh(ReviewSummary())


def test_reviews_survive_a_supplier_roundtrip_through_the_database():
    from app import db
    s = sup()
    s.reviews = build_summary(tool_input(), {PAGE_A: TEXT_A})
    db.save_supplier(s.domain, s.model_dump(), "h")
    back = Supplier(**db.get_supplier_row(s.domain)["data"])
    assert back.reviews.found and back.reviews.positives[0].quote.startswith("Отличные цены")
