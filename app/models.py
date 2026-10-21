from __future__ import annotations

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """Одно значение с доказательством: цитата со страницы и адрес страницы."""

    value: str
    quote: str = ""
    url: str = ""


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
    price_info: Fact | None = None
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
