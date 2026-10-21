import asyncio
import io

import httpx
from pypdf import PdfWriter

from app import fetch
from app.extract import build_prompt
from app.fetch import fetch_site, find_subpages
from app.verify import norm, quote_in_text


def make_pdf(lines: list[str]) -> bytes:
    """Минимальный PDF с текстовым слоем (синтетика для тестов)."""
    stream = "BT /F1 12 Tf 50 750 Td " + " ".join(f"({t}) Tj 0 -16 Td" for t in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


PRICE_PDF = make_pdf(["Price list from 01.10.2026", "Flour premium 25 kg - 48.00 RUB/kg", "Sugar 50 kg - 61.00 RUB/kg",
                      "Minimum order 100 kg. Delivery free from 50 000 RUB."])

HOME = """<html><body>
<a href="/contacts">Контакты</a>
<a href="/upload/price.pdf">ПРАЙС</a>
<a href="/files/katalog-2026.pdf">Скачать</a>
<a href="/upload/Правила_применения_рекомендательных_технологий.pdf">Правила</a>
<a href="/upload/presentation.pdf">Скачать презентацию</a>
<a href="/docs/privacy-policy-price.pdf">Политика конфиденциальности</a>
<a href="https://other.example/price.pdf">Чужой прайс</a>
<a href="/extra/price2.pdf">Прайс 2</a>
</body></html>"""


def test_only_price_like_pdfs_on_same_host_are_picked():
    subs = find_subpages(HOME, "https://shop.ru/")
    pdfs = [u for u in subs if u.endswith(".pdf")]
    assert pdfs == ["https://shop.ru/upload/price.pdf", "https://shop.ru/files/katalog-2026.pdf"]      # не больше двух, по порядку
    assert "https://shop.ru/contacts" in subs
    assert not any("presentation" in u or "privacy" in u or "other.example" in u or "Правила" in u for u in subs)


def test_transliterated_catalog_and_url_encoded_names():
    html = '<a href="/wp-content/uploads/rybnoe-napravlenie-katalog.pdf">Рыбный</a><a href="/%D0%BF%D1%80%D0%B0%D0%B9%D1%81.pdf">Скачать</a>'
    assert len([u for u in find_subpages(html, "https://shop.ru/") if u.endswith(".pdf")]) == 2


def make_client(files: dict[str, tuple[bytes, str]], hits: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        body, ctype = files.get(request.url.path, (b"nope", "text/plain"))
        return httpx.Response(200 if request.url.path in files else 404, content=body, headers={"content-type": ctype})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_pdf_price_is_fetched_read_and_quotable():
    hits: list[str] = []
    files = {"/": (HOME.encode("utf-8"), "text/html; charset=utf-8"), "/upload/price.pdf": (PRICE_PDF, "application/pdf"),
             "/files/katalog-2026.pdf": (make_pdf(["Catalog Flour premium 48.00 RUB/kg " * 3]), "application/pdf"),
             "/contacts": (b"<html>Tel +7 (812) 123-45-67</html>", "text/html")}
    hits.clear()

    async def run():
        async with make_client(files, hits) as c:
            return await fetch_site("https://shop.ru/", client=c)

    pages = asyncio.run(run())
    pdf_url = "https://shop.ru/upload/price.pdf"
    assert pdf_url in pages and "Flour premium" in pages[pdf_url]
    # цена из PDF подтверждается так же, как цитата со страницы
    assert quote_in_text("Flour premium 25 kg - 48.00 RUB/kg", norm(pages[pdf_url]))
    assert "/upload/presentation.pdf" not in hits and "/docs/privacy-policy-price.pdf" not in hits    # лишнее не качали

    hits.clear()
    asyncio.run(run())                    # второй раз всё из кэша: сайт не дёргаем
    assert hits == []


def test_scans_big_and_fake_pdfs_are_skipped(monkeypatch):
    blank = PdfWriter(); blank.add_blank_page(100, 100); buf = io.BytesIO(); blank.write(buf)
    files = {"/": ('<a href="/price.pdf">Прайс</a><a href="/catalog.pdf">Каталог</a>'.encode("utf-8"), "text/html; charset=utf-8"),
             "/price.pdf": (buf.getvalue(), "application/pdf"),                       # скан: текста нет
             "/catalog.pdf": (b"<html>not a pdf</html>", "application/pdf")}           # подделка

    async def run():
        async with make_client(files, []) as c:
            return await fetch_site("https://shop.ru/", client=c)

    assert [u for u in asyncio.run(run()) if u.endswith(".pdf")] == []

    monkeypatch.setattr(fetch, "MAX_PDF_BYTES", 500)                                 # слишком большой файл
    files["/price.pdf"] = (PRICE_PDF, "application/pdf")
    assert [u for u in asyncio.run(run()) if u.endswith(".pdf")] == []


def test_pdf_text_keeps_its_own_budget_in_prompt():
    pages = {"https://s.ru/": "ГЛАВНАЯ " * 6000, "https://s.ru/delivery": "ДОСТАВКА " * 6000,
             "https://s.ru/price.pdf": "PRICE-LINE 48.00 " * 600}
    prompt = build_prompt(pages)
    assert "PRICE-LINE 48.00" in prompt and "[PAGE https://s.ru/price.pdf]" in prompt
    assert len(prompt) < 40_000
    # длинная главная не вытесняет страницу доставки: обе страницы присутствуют
    assert "[PAGE https://s.ru/delivery]" in prompt and "ДОСТАВКА" in prompt


def test_allocate_is_fair_and_respects_budget():
    from app.extract import allocate
    assert allocate([100, 100], 1000) == [100, 100]                      # короткие целиком
    assert allocate([50_000, 50_000, 50_000], 24_000) == [8000, 8000, 8000]
    assert allocate([500, 50_000, 50_000], 10_500) == [500, 5000, 5000]  # неиспользованное отдают длинным
    assert sum(allocate([9000, 9000, 9000, 9000], 20_000)) <= 20_000 and allocate([], 100) == []
