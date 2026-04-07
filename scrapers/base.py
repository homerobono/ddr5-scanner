"""Base scraper class and shared data models."""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from utils.logging import get_logger

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


@dataclass
class Listing:
    source: str
    title: str
    url: str
    price: float | None = None
    raw_price: str = ""
    description: str = ""
    image_url: str = ""
    seller: str = ""
    condition: str = "new"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassifiedListing:
    listing: Listing
    is_match: bool = False
    confidence: float = 0.0
    reason: str = ""
    brand: str = ""
    model: str = ""
    capacity_gb: int | None = None
    speed_mhz: int | None = None
    cas_latency: int | None = None
    kit_count: int = 1


@dataclass
class ScraperResult:
    source: str
    listings: list[Listing]
    error: str | None = None


class BaseScraper(ABC):
    name: str = "base"
    max_retries: int = 3
    base_delay: float = 1.0

    def __init__(self, config: dict) -> None:
        self.config = config
        scraper_cfg = config.get("scrapers", {})
        self.delay = scraper_cfg.get("request_delay_seconds", 2)
        self.headless = scraper_cfg.get("headless", True)
        self.search_queries: list[str] = config.get(
            "search_queries", ["memoria ddr5 cl30"]
        )
        self.log = get_logger(f"scraper.{self.name}")

    def random_ua(self) -> str:
        return random.choice(USER_AGENTS)

    def default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = await client.request(method, url, **kwargs)
                if resp.status_code == 429:
                    wait = self.base_delay * (2**attempt) + random.uniform(0, 1)
                    self.log.warning(f"Rate-limited (429), waiting {wait:.1f}s...")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    wait = self.base_delay * (2**attempt) + random.uniform(0, 1)
                    self.log.warning(
                        f"Request failed (attempt {attempt + 1}), retrying in {wait:.1f}s: {exc}"
                    )
                    await asyncio.sleep(wait)
        raise last_exc or RuntimeError("Request failed after retries")

    async def throttle(self) -> None:
        jitter = random.uniform(0, self.delay * 0.5)
        await asyncio.sleep(self.delay + jitter)

    def parse_brl_price(self, text: str) -> float | None:
        """Parse a Brazilian Real price string like 'R$ 1.299,90' into 1299.90."""
        import re

        if not text:
            return None
        cleaned = re.sub(r"[^\d.,]", "", text.strip())
        if not cleaned:
            return None
        cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    @abstractmethod
    async def search(self) -> list[Listing]:
        """Run search across all configured queries and return raw listings."""
        ...
