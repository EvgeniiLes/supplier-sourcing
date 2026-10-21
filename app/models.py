from __future__ import annotations

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """Одно значение с доказательством: цитата со страницы и адрес страницы."""

    value: str
    quote: str = ""
    url: str = ""


class PriceItem(BaseModel):
    """Цена на искомый товар, найденная на сайте или в прайсе. Цитата и цена проверены по тексту."""

    name: str
    price: float
    unit: str = ""           # только если единица стоит рядом с ценой в тексте (₽/кг, за шт); иначе пусто
    pack: str = ""
    quote: str = ""
    url: str = ""
    base_price: float | None = None    # цена за кг или литр для сравнения поставщиков
    base_unit: str = ""
    derived: bool = False              # True: цена за кг/л рассчитана из цены за упаковку/штуку и фасовки


class Supplier(BaseModel):
    domain: str
    url: str
    also_domains: list[str] = Field(default_factory=list)
    name: Fact | None = None
    offering: Fact | None = None
    address: Fact | None = None
    regions: list[Fact] = Field(default_factory=list)
    nationwide: Fact | None = None
    phones: list[Fact] = Field(default_factory=list)
    emails: list[Fact] = Field(default_factory=list)
    inn: Fact | None = None
    min_order: Fact | None = None
    min_order_kg: float | None = None
    price_info: Fact | None = None          # общие условия по ценам (есть ли прайс, цены по запросу, скидки)
    query_prices: list[PriceItem] | None = None   # None: извлечение не знало запроса; []: искали, цен на товар нет
    query_for: str = ""                     # для какого запроса извлечены query_prices
    certificates: list[Fact] = Field(default_factory=list)
    delivery: Fact | None = None
    wholesale: Fact | None = None
    sources: list[str] = Field(default_factory=list)
    rejected_facts: int = 0

    def fact_count(self) -> int:
        single = [self.name, self.offering, self.address, self.nationwide, self.inn,
                  self.min_order, self.price_info, self.delivery, self.wholesale]
        return (sum(1 for f in single if f) + len(self.regions) + len(self.phones)
                + len(self.emails) + len(self.certificates))


class Filters(BaseModel):
    need_certs: bool = False
    max_min_order_kg: float | None = None
    delivery_to_region: bool = False


class ScoreResult(BaseModel):
    score: int
    pros: list[str]
    cons: list[str]
    missing: list[str]
    passes: bool = True
    fail_reasons: list[str] = Field(default_factory=list)
    breakdown: list[dict] = Field(default_factory=list)
