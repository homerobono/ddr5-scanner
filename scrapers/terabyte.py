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
            context = await self._create_stealth_context(browser)
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
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if resp and resp.status in (403, 503):
            self.log.warning(f"Got {resp.status} for '{query}', waiting before retry...")
            await asyncio.sleep(5)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp and resp.status in (403, 503):
                self.log.warning(f"Still {resp.status} for '{query}', skipping")
                return

        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(2)

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)

        html = await page.content()
        self._dump_debug_html(html, query)
        soup = BeautifulSoup(html, "lxml")

        cards = soup.select(
            "#prodarea .pbox, .products-area .pbox, .pbox, "
            "[class*='product-card'], [class*='prod-item'], "
            "div[data-product-id]"
        )

        if not cards:
            cards = soup.select("a[href*='/produto/']")
            self.log.debug(
                f"Fallback: found {len(cards)} product links for '{query}'"
            )

        if not cards:
            self.log.debug(f"No product cards found for '{query}'")
            return

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        link_el = card.select_one("a[href*='/produto/']")
        if not link_el:
            link_el = card.select_one("a[href]")
        if not link_el:
            if card.name == "a" and card.get("href"):
                link_el = card
            else:
                return None

        href = link_el.get("href", "")
        if not href:
            return None
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        title_el = card.select_one(
            "h2.prod-name, .prod-name, h2, h3, a[title], "
            "[class*='product-name'], [class*='prod-title']"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title and link_el.get("title"):
            title = link_el["title"]
        if not title:
            title = link_el.get_text(strip=True)
        if not title:
            return None

        price_el = card.select_one(
            ".prod-new-price span, .prod-new-price, "
            ".val-prod, .prod-price, .price-destaque, "
            "[class*='price'] span, [class*='price']"
        )
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price = self.parse_brl_price(raw_price)

        if not price:
            for el in card.select("span, div, p"):
                text = el.get_text(strip=True)
                if "R$" in text:
                    price = self.parse_brl_price(text)
                    if price:
                        raw_price = text
                        break

        img_el = card.select_one("img[src], img[data-src]")
        image_url = ""
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src") or ""

        return Listing(
            source="terabyte",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="new",
        )
