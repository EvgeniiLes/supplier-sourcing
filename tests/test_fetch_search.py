from app.fetch import clean_html, find_subpages
from app.search import domain_of, is_blacklisted, fallback_queries

# Синтетический HTML для тестов
HTML = """<html><head><title>Тест</title><script>var x=1</script></head><body>
<nav><a href="/contacts">Контакты</a><a href="/delivery/">Доставка и оплата</a><a href="/img/a.png">pic</a>
<a href="https://other.ru/contacts">Контакты</a></nav>
<p>Звоните <a href="tel:+78121234567">+7 812 123-45-67</a></p>
<a href="mailto:info@t.ru?subject=1">Напишите</a><footer>ИНН 7701234567</footer></body></html>"""


def test_clean_html_keeps_footer_and_link_contacts():
    text, title = clean_html(HTML)
    assert title == "Тест" and "var x" not in text
    assert "ИНН 7701234567" in text and "info@t.ru" in text and "+78121234567" in text


def test_clean_html_detects_windows_1251():
    raw = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1251"></head><body><p>Доставка по Москве</p></body></html>'.encode("cp1251")
    assert "Доставка по Москве" in clean_html(raw)[0]


def test_find_subpages_same_host_only():
    subs = find_subpages(HTML, "https://t.ru/")
    assert subs == ["https://t.ru/contacts", "https://t.ru/delivery/"]


def test_domain_and_blacklist():
    assert domain_of("https://www.shop.example.ru/a") == "example.ru"
    assert domain_of("https://eastfood.spb.ru/product/x") == "eastfood.spb.ru"
    assert domain_of("https://www.msk.ru") == "msk.ru"
    assert is_blacklisted("https://www.avito.ru/x") and is_blacklisted("https://sub.ozon.ru/y")
    assert not is_blacklisted("https://moloko-opt.ru")


def test_fallback_queries_contain_region():
    assert all("Казань" in q for q in fallback_queries("сыр", "Казань"))
