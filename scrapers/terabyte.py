"""Terabyte Shop scraper using Playwright for JS rendering."""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class TerabyteScraper(BaseScraper):
    name = "terabyte"
    BASE_URL = "https://www.terabyteshop.com.br"

    async def search(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_urls: set[str] = set()

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.log.error(
                "Playwright not installed. Run: pip install playwright && playwright install"
            )
            return []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            context = await browser.new_context(
                user_agent=self.random_ua(),
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['pt-BR', 'pt', 'en']
                });
            """)

            page = await context.new_page()

            for query in self.search_queries:
                try:
                    await self._search_query(page, query, listings, seen_urls)
                except Exception as exc:
                    self.log.warning(f"Query '{query}' failed: {exc}")
                await self.throttle()

            await browser.close()

        return listings

    async def _search_query(
        self,
        page,
        query: str,
        listings: list[Listing],
        seen_urls: set[str],
    ) -> None:
        url = f"{self.BASE_URL}/busca?str={quote_plus(query)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        try:
            await page.wait_for_selector(
                ".pbox, .product-item, .product-card",
                timeout=10000,
            )
        except Exception:
            self.log.debug(f"No product cards found for '{query}'")
            return

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        cards = soup.select(".pbox, .product-item, .product-card")

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        link_el = card.select_one("a[href]")
        if not link_el:
            return None

        href = link_el.get("href", "")
        if not href:
            return None
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        title_el = card.select_one("h2.prod-name, .prod-name, h2")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        price_el = card.select_one(
            ".prod-new-price, .val-prod, .prod-price"
        )
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price = self.parse_brl_price(raw_price)

        img_el = card.select_one("img[src]")
        image_url = img_el["src"] if img_el else ""

        return Listing(
            source="terabyte",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="new",
        )
