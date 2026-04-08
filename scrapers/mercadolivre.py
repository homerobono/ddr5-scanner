"""Mercado Livre scraper using Playwright to bypass API auth requirements."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class MercadoLivreScraper(BaseScraper):
    name = "mercadolivre"
    BASE_URL = "https://lista.mercadolivre.com.br"

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
        url = f"{self.BASE_URL}/{quote_plus(query).replace('+', '-')}#D[A:{quote_plus(query)}]"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if resp and resp.status in (403, 503):
            self.log.warning(f"Got {resp.status} for '{query}', waiting before retry...")
            await asyncio.sleep(5)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp and resp.status in (403, 503):
                self.log.warning(f"Still {resp.status} for '{query}', skipping")
                return

        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        cards = soup.select(
            ".ui-search-result, .ui-search-layout__item, "
            ".andes-card[data-testid], li.ui-search-layout__item"
        )

        for card in cards[:50]:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        link = card.select_one(
            "a.ui-search-link, a.ui-search-item__group__element, "
            "a[href*='mercadolivre.com.br/']"
        )
        if not link or not link.get("href"):
            return None

        href = link["href"]
        if not href.startswith("http"):
            return None

        title_el = card.select_one(
            ".ui-search-item__title, h2.ui-search-item__title, "
            "a.ui-search-link__title-card"
        )
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        if not title:
            return None

        price_el = card.select_one(
            ".ui-search-price__second-line .andes-money-amount__fraction, "
            ".andes-money-amount__fraction"
        )
        raw_price = ""
        price = None
        if price_el:
            fraction_text = price_el.get_text(strip=True)
            cents_el = card.select_one(".andes-money-amount__cents")
            cents = cents_el.get_text(strip=True) if cents_el else "00"
            raw_price = f"R$ {fraction_text},{cents}"
            cleaned = fraction_text.replace(".", "")
            try:
                price = float(f"{cleaned}.{cents}")
            except ValueError:
                price = self.parse_brl_price(raw_price)

        condition_el = card.select_one(
            ".ui-search-item__group__element--subtitle, "
            ".ui-search-item__subtitle"
        )
        condition_text = condition_el.get_text(strip=True).lower() if condition_el else ""
        condition = "used" if re.search(r"usado|seminovo", condition_text) else "new"

        seller_el = card.select_one(
            ".ui-search-official-store-label, .ui-search-item__seller"
        )
        seller = seller_el.get_text(strip=True) if seller_el else ""

        img_el = card.select_one("img[src], img[data-src]")
        image_url = ""
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src") or ""

        return Listing(
            source="mercadolivre",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            seller=seller,
            condition=condition,
        )
