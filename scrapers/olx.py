"""OLX Brasil scraper using Playwright to bypass Cloudflare."""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class OLXScraper(BaseScraper):
    name = "olx"
    BASE_URL = "https://www.olx.com.br"

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

            # Visit homepage first to get cookies / pass initial challenge
            try:
                await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
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
        url = f"{self.BASE_URL}/informatica?q={quote_plus(query)}&o=1"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if resp and resp.status in (403, 503):
            self.log.warning(f"Got {resp.status} for '{query}', waiting before retry...")
            await asyncio.sleep(8)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp and resp.status in (403, 503):
                self.log.warning(f"Still {resp.status} for '{query}', skipping")
                return

        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # Handle possible cookie consent
        try:
            consent = await page.query_selector(
                "button[id*='accept'], button[id*='consent'], "
                "[data-ds-component='DS-Button']"
            )
            if consent:
                text = await consent.text_content()
                if text and ("aceitar" in text.lower() or "concordo" in text.lower()):
                    await consent.click()
                    await asyncio.sleep(1)
        except Exception:
            pass

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

        html = await page.content()
        self._dump_debug_html(html, query)
        soup = BeautifulSoup(html, "lxml")

        cards = soup.select(
            '[data-ds-component="DS-AdCard"], '
            '[data-ds-component="DS-NewAdCard-Link"], '
            "a.olx-ad-card, a[href*='/item/']"
        )

        if not cards:
            cards = soup.select("section a[href*='olx.com.br']")
            cards = [c for c in cards if "/item/" in c.get("href", "")]

        if not cards:
            all_links = soup.select("a[href]")
            cards = [
                a for a in all_links
                if "/item/" in a.get("href", "") and a.get_text(strip=True)
            ]
            if cards:
                self.log.debug(f"Deep fallback: found {len(cards)} item links")

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        link = card if card.name == "a" else card.find("a", href=True)
        if not link or not link.get("href"):
            return None

        href = link["href"]
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        # Strip tracking params
        if "?" in href:
            href = href.split("?")[0]

        title_el = card.select_one(
            "h2, h3, [data-ds-component='DS-Text'], .title, "
            "[class*='AdCard'] h2, [class*='title']"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            title = link.get_text(strip=True)
        if not title:
            return None

        raw_price = ""
        for el in card.select("span, p, div"):
            text = el.get_text(strip=True)
            if "R$" in text:
                raw_price = text
                break

        price = self.parse_brl_price(raw_price)

        img_el = card.select_one("img[src], img[data-src]")
        image_url = ""
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src") or ""

        return Listing(
            source="olx",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="used",
        )
