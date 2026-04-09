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
            context = await self._create_stealth_context(browser)
            page = await context.new_page()

            # Visit google.com.br first to get cookies
            try:
                await page.goto(
                    "https://www.google.com.br", wait_until="domcontentloaded", timeout=10000
                )
                await asyncio.sleep(1)
                # Dismiss cookie banner
                try:
                    accept_btn = await page.query_selector(
                        "button[id*='accept'], button[id*='L2AGLb'], "
                        "button[aria-label*='Aceitar']"
                    )
                    if accept_btn:
                        await accept_btn.click()
                        await asyncio.sleep(1)
                except Exception:
                    pass
            except Exception:
                pass

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
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # Scroll multiple times to trigger lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

        html = await page.content()
        self._dump_debug_html(html, query)
        soup = BeautifulSoup(html, "lxml")

        # Google Shopping uses obfuscated class names that change frequently;
        # try multiple known patterns plus structural selectors
        cards = soup.select(
            ".sh-dgr__gr-auto, .sh-dlr__list-result, "
            "div[data-docid], .sh-pr__product-results-grid div[data-docid], "
            ".KZmu8e, .i0X6df, .u30d4, .sh-dgr__content"
        )

        if not cards:
            cards = soup.select("a[href*='/shopping/product/']")

        if not cards:
            # Broader fallback: find divs that look like product cards
            # (contain both a link and a price-like text)
            for div in soup.select("div"):
                link = div.find("a", href=True)
                text = div.get_text()
                if link and "R$" in text and len(text) < 500:
                    has_child_div_with_link = div.find("div") is not None
                    if has_child_div_with_link:
                        cards.append(div)
            # Deduplicate by removing cards that are children of other cards
            if cards:
                cards = self._deduplicate_nested(cards)

        for card in cards[:40]:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _deduplicate_nested(self, cards: list) -> list:
        """Remove cards that are descendants of other cards in the list."""
        result = []
        for card in cards:
            is_child = False
            for other in cards:
                if other is not card and other in card.parents:
                    is_child = True
                    break
            if not is_child:
                result.append(card)
        return result[:40]

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        link = card.find("a", href=True)
        if not link:
            return None

        href = link["href"]
        if href.startswith("/url?"):
            match = re.search(r"[?&]url=([^&]+)", href)
            if match:
                href = unquote(match.group(1))
        if href.startswith("/shopping/"):
            href = f"https://www.google.com.br{href}"
        if not href.startswith("http"):
            href = f"https://www.google.com.br{href}"

        title_el = card.select_one(
            "h3, h4, .tAxDx, [data-name], .Xjkr3b, "
            "[role='heading'], a[aria-label]"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            title = link.get("aria-label") or link.get_text(strip=True)
        if not title:
            return None

        # Find price: look for text containing R$
        raw_price = ""
        price = None
        price_el = card.select_one(
            ".a8Pemb, .HRLxBb, [data-price], .kHxwFf, "
            "span b, .sh-dgr__content .a8Pemb"
        )
        if price_el:
            raw_price = price_el.get_text(strip=True)
            price = self.parse_brl_price(raw_price)

        if not price:
            for el in card.select("span, b, div"):
                text = el.get_text(strip=True)
                if "R$" in text and len(text) < 30:
                    parsed = self.parse_brl_price(text)
                    if parsed:
                        price = parsed
                        raw_price = text
                        break

        seller_el = card.select_one(
            ".aULzUe, .IuHnof, .E5ocAb, .b5ycib, .sh-dgr__content .E5ocAb"
        )
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
