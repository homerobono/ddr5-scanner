"""Facebook Marketplace scraper using Playwright with stealth settings."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus

from scrapers.base import BaseScraper, Listing


class FacebookScraper(BaseScraper):
    name = "facebook"
    BASE_URL = "https://www.facebook.com/marketplace"

    async def search(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_urls: set[str] = set()

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.log.error("Playwright not installed. Run: pip install playwright && playwright install")
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
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en']});
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
        # Facebook Marketplace search URL for Brazil (São Paulo region)
        url = (
            f"{self.BASE_URL}/saopaulo/search?"
            f"query={quote_plus(query)}&exact=false"
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

        cards = await page.query_selector_all(
            'a[href*="/marketplace/item/"]'
        )

        for card in cards[:30]:
            try:
                listing = await self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    async def _parse_card(self, card) -> Listing | None:
        href = await card.get_attribute("href") or ""
        if not href:
            return None
        if not href.startswith("http"):
            href = f"https://www.facebook.com{href}"

        text_content = await card.inner_text()
        lines = [l.strip() for l in text_content.split("\n") if l.strip()]

        title = ""
        raw_price = ""
        for line in lines:
            if re.search(r"R\$\s*[\d.,]+", line):
                raw_price = line
            elif not title and len(line) > 5:
                title = line

        if not title and lines:
            title = lines[0]

        price = self.parse_brl_price(raw_price)

        img = await card.query_selector("img[src]")
        image_url = await img.get_attribute("src") if img else ""

        return Listing(
            source="facebook",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url or "",
            condition="used",
        )
