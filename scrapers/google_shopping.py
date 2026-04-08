"""Google Shopping scraper using Playwright for JS-rendered results."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus, unquote

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class GoogleShoppingScraper(BaseScraper):
    name = "google_shopping"
    SEARCH_URL = "https://www.google.com.br/search"

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
        url = (
            f"{self.SEARCH_URL}?tbm=shop&q={quote_plus(query)}"
            f"&gl=br&hl=pt-BR"
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await asyncio.sleep(2)

        # Accept cookies if banner appears
        try:
            accept_btn = await page.query_selector(
                "button[id*='accept'], button[aria-label*='Aceitar']"
            )
            if accept_btn:
                await accept_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        cards = soup.select(
            ".sh-dgr__gr-auto, .sh-dlr__list-result, "
            "div[data-docid], .sh-pr__product-results-grid div[data-docid], "
            ".KZmu8e, .i0X6df"
        )

        if not cards:
            cards = soup.select("div.sh-dgr__content, a[href*='/shopping/product/']")

        for card in cards[:30]:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        link = card.find("a", href=True)
        if not link:
            return None

        href = link["href"]
        if href.startswith("/url?"):
            match = re.search(r"[?&]url=([^&]+)", href)
            if match:
                href = unquote(match.group(1))
        if not href.startswith("http"):
            href = f"https://www.google.com.br{href}"

        title_el = card.select_one("h3, h4, .tAxDx, [data-name], .Xjkr3b")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            title = link.get_text(strip=True)
        if not title:
            return None

        price_el = card.select_one(
            ".a8Pemb, .HRLxBb, [data-price], span b, .kHxwFf"
        )
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price = self.parse_brl_price(raw_price)

        seller_el = card.select_one(".aULzUe, .IuHnof, .E5ocAb, .b5ycib")
        seller = seller_el.get_text(strip=True) if seller_el else ""

        img_el = card.select_one("img[src]")
        image_url = img_el["src"] if img_el else ""

        return Listing(
            source="google_shopping",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            seller=seller,
            condition="new",
        )
