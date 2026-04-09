"""Base scraper class and shared data models."""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from utils.logging import get_logger

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
]

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en']});
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
"""


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
        self.debug_html = scraper_cfg.get("debug_html", False)
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
        retryable_statuses = {429, 403, 503}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = await client.request(method, url, **kwargs)
                if resp.status_code in retryable_statuses:
                    wait = self.base_delay * (2 ** (attempt + 1)) + random.uniform(0, 2)
                    self.log.warning(
                        f"HTTP {resp.status_code} for {url}, "
                        f"waiting {wait:.1f}s (attempt {attempt + 1}/{self.max_retries})..."
                    )
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

    def _dump_debug_html(self, html: str, query: str) -> None:
        """Save raw HTML to data/debug/ for troubleshooting broken selectors."""
        if not self.debug_html:
            return
        debug_dir = Path("data/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_q = query.replace(" ", "_")[:30]
        path = debug_dir / f"{self.name}_{safe_q}_{ts}.html"
        path.write_text(html, encoding="utf-8")
        self.log.debug(f"Debug HTML saved to {path}")

    async def _create_stealth_context(self, browser, *, extra_args: dict | None = None):
        """Create a Playwright browser context with stealth settings."""
        ctx_args = {
            "user_agent": self.random_ua(),
            "locale": "pt-BR",
            "timezone_id": "America/Sao_Paulo",
            "viewport": {"width": 1920, "height": 1080},
            "extra_http_headers": {
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        }
        if extra_args:
            ctx_args.update(extra_args)
        context = await browser.new_context(**ctx_args)
        await context.add_init_script(STEALTH_INIT_SCRIPT)
        return context

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
