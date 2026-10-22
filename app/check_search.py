"""Проверка поисковых источников без запуска сервера и без трат на разбор сайтов.

python -m app.check_search "хорека" "Екатеринбург"

Показывает: какие источники включены, какие запросы уходят, что отвечает каждый источник (или текст ошибки)
и итоговый список кандидатов. Удобно, чтобы проверить ключ Яндекса.
"""
import asyncio
import sys

from .config import get_settings
from .search import build_queries, expand_queries, find_candidates, tavily_search, yandex_region_id, yandex_search


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Использование: python -m app.check_search "товар" ["город"]')
    category = sys.argv[1]
    region = sys.argv[2] if len(sys.argv) > 2 else ""
    s = get_settings()
    print(f"Источники: {', '.join(s.sources) or 'нет'} "
          f"(Яндекс: {'ключ и каталог заданы' if s.yandex_enabled else 'не настроен'}, Tavily: {'ключ задан' if s.tavily_api_key else 'нет ключа'})")
    if region:
        print(f"Код региона Яндекса для «{region}»: {yandex_region_id(region) or 'не из списка, регион только в тексте запроса'}")

    queries = build_queries(category, region, await expand_queries(category, region) if s.anthropic_api_key else [])
    print("\nЗапросы:")
    for q in queries:
        print("  •", q)

    first = queries[0]
    for name, call in (("Яндекс", yandex_search), ("Tavily", tavily_search)):
        if (name == "Яндекс" and not s.yandex_enabled) or (name == "Tavily" and not s.tavily_api_key):
            continue
        print(f"\n{name}, первый запрос «{first}»:")
        try:
            items = await (call(first, region) if name == "Яндекс" else call(first))
            print(f"  ответ получен, результатов: {len(items)}")
            for it in items[:5]:
                print(f"    {it['url'][:70]:70} {it.get('title', '')[:50]!r}")
        except Exception as e:                                   # noqa: BLE001  нужно показать любую причину
            print(f"  ОШИБКА: {e}")

    print("\nИтоговые кандидаты (после слияния источников и отсева агрегаторов и зарубежных):")
    try:
        found = await find_candidates(queries, s.max_suppliers, region=region)
    except RuntimeError as e:
        raise SystemExit(f"  ОШИБКА: {e}")
    print(f"  источники в выдаче: {found.sources_used}; ошибки источников: {found.source_errors or 'нет'}; "
          f"зарубежных отсеяно: {found.skipped_foreign}")
    for c in found:
        print(f"    {c['domain']:32} {c['title'][:60]!r}")


if __name__ == "__main__":
    asyncio.run(main())
