"""OLX Brasil scraper using HTTP requests + BeautifulSoup."""

from __future__ import annotations

from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class OLXScraper(BaseScraper):
    name = "olx"
    BASE_URL = "https://www.olx.com.br"

    async def search(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True
        ) as client:
            for query in self.search_queries:
                try:
                    await self._search_query(client, query, listings, seen_urls)
                except Exception as exc:
                    self.log.warning(f"Query '{query}' failed: {exc}")
                await self.throttle()

        return listings

    async def _search_query(
        self,
        client: httpx.AsyncClient,
        query: str,
        listings: list[Listing],
        seen_urls: set[str],
    ) -> None:
        for page_num in range(1, 4):
            url = f"{self.BASE_URL}/informatica?q={quote_plus(query)}&o={page_num}"
            resp = await self._request_with_retry(
                client, "GET", url, headers=self.default_headers()
            )
            soup = BeautifulSoup(resp.text, "lxml")

            cards = soup.select(
                '[data-ds-component="DS-NewAdCard-Link"], '
                "a.olx-ad-card, a[href*='/item/']"
            )
            if not cards:
                break

            for card in cards:
                try:
                    listing = self._parse_card(card, soup)
                    if listing and listing.url not in seen_urls:
                        seen_urls.add(listing.url)
                        listings.append(listing)
                except Exception as exc:
                    self.log.debug(f"Failed to parse card: {exc}")

            await self.throttle()

    def _parse_card(self, card: BeautifulSoup, page_soup: BeautifulSoup) -> Listing | None:
        link = card if card.name == "a" else card.find("a", href=True)
        if not link or not link.get("href"):
            return None

        href = link["href"]
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        title_el = card.select_one(
            "h2, h3, [data-ds-component='DS-Text'], .title"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        price_el = card.select_one(
            "[data-ds-component='DS-Text']:has(+ *), .price, span"
        )
        raw_price = ""
        if price_el:
            text = price_el.get_text(strip=True)
            if "R$" in text:
                raw_price = text
        if not raw_price:
            for span in card.select("span"):
                text = span.get_text(strip=True)
                if "R$" in text:
                    raw_price = text
                    break

        price = self.parse_brl_price(raw_price)

        img_el = card.select_one("img[src]")
        image_url = img_el["src"] if img_el else ""

        return Listing(
            source="olx",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="used",
        )
