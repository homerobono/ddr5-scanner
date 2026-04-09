"""Amazon BR scraper using Playwright to bypass bot detection."""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class AmazonScraper(BaseScraper):
    name = "amazon"
    BASE_URL = "https://www.amazon.com.br"

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
                    "--disable-dev-shm-usage",
                ],
            )
            context = await self._create_stealth_context(browser)
            page = await context.new_page()

            # Visit homepage first to establish cookies and bypass initial check
            try:
                await page.goto(
                    self.BASE_URL, wait_until="domcontentloaded", timeout=15000
                )
                await asyncio.sleep(3)
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
        url = f"{self.BASE_URL}/s?k={quote_plus(query)}&page=1"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if resp and resp.status in (403, 503):
            self.log.warning(f"Got {resp.status} for '{query}', waiting before retry...")
            await asyncio.sleep(8)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp and resp.status in (403, 503):
                self.log.warning(f"Still {resp.status} for '{query}', skipping")
                return

        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # Handle CAPTCHA page
        captcha = await page.query_selector(
            "form[action*='validateCaptcha'], form[action*='captcha'], "
            "input[id='captchacharacters']"
        )
        if captcha:
            self.log.warning(f"CAPTCHA detected for '{query}', skipping")
            return

        # Check for bot detection page
        page_text = await page.text_content("body")
        if page_text and (
            "robot" in page_text.lower()
            or "automated" in page_text.lower()
        ):
            self.log.warning(f"Bot detection page for '{query}', skipping")
            return

        # Scroll to trigger lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

        html = await page.content()
        self._dump_debug_html(html, query)
        soup = BeautifulSoup(html, "lxml")

        cards = soup.select('[data-component-type="s-search-result"]')

        if not cards:
            cards = soup.select(
                ".s-result-item[data-asin], "
                "div[data-asin]:not([data-asin=''])"
            )

        if not cards:
            # Last resort: find product links
            links = soup.select("a[href*='/dp/']")
            self.log.debug(f"Fallback: found {len(links)} /dp/ links")
            for link in links:
                parent = link.find_parent("div", {"data-asin": True}) or link.parent
                if parent and parent not in cards:
                    cards.append(parent)

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        title_el = card.select_one(
            "h2 a span, h2 span.a-text-normal, "
            "h2 span, .a-link-normal span.a-text-normal"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        link_el = card.select_one("h2 a[href], a.a-link-normal[href*='/dp/']")
        if not link_el:
            return None
        href = link_el["href"]
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        whole = card.select_one(".a-price .a-price-whole")
        fraction = card.select_one(".a-price .a-price-fraction")
        if whole:
            price_text = whole.get_text(strip=True).replace(".", "").replace(",", "")
            frac_text = fraction.get_text(strip=True) if fraction else "00"
            raw_price = f"R$ {price_text},{frac_text}"
            try:
                price = float(f"{price_text}.{frac_text}")
            except ValueError:
                price = None
        else:
            price_el = card.select_one(
                ".a-price, .a-offscreen, span[data-a-color='price']"
            )
            raw_price = price_el.get_text(strip=True) if price_el else ""
            price = self.parse_brl_price(raw_price)

        if not price:
            for el in card.select("span.a-offscreen, span.a-price"):
                text = el.get_text(strip=True)
                if "R$" in text or text.replace(".", "").replace(",", "").isdigit():
                    parsed = self.parse_brl_price(text)
                    if parsed:
                        price = parsed
                        raw_price = text
                        break

        img_el = card.select_one("img.s-image, img[data-image-latency='s-product-image']")
        image_url = img_el["src"] if img_el else ""

        seller_el = card.select_one(
            ".a-size-small .a-color-secondary, "
            "span[class*='a-size-small']"
        )
        seller = seller_el.get_text(strip=True) if seller_el else ""

        return Listing(
            source="amazon",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            seller=seller,
            condition="new",
        )
