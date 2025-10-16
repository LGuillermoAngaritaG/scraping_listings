from typing import Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class SimpleInfo(BaseModel):
    name: str
    xpath: str


class DynamicInfo(BaseModel):
    name: str
    xpath_names: str  # XPath that extracts a list, or plain string
    xpath_values: str  # XPath that extracts a list, or plain string


class ScraperInput(BaseModel):
    name: str
    description: str | None = None
    urls: list[str]
    base_url: str | None = None
    next_url_xpath: str | None = None
    number_of_pages: int = 1
    information: list[SimpleInfo | DynamicInfo]
    scrapying_engine: Literal['scrapy', 'playwright'] = "scrapy"


class ScraperOutput(BaseModel):
    url: str
    information: dict[str, Any]
    date_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
