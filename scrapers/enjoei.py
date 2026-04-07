"""Enjoei scraper using HTTP requests + BeautifulSoup."""

from __future__ import annotations

from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class EnjoeiScraper(BaseScraper):
    name = "enjoei"
    BASE_URL = "https://www.enjoei.com.br"

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
        search_url = f"{self.BASE_URL}/s/{quote_plus(query)}"
        resp = await self._request_with_retry(
            client, "GET", search_url, headers=self.default_headers()
        )
        soup = BeautifulSoup(resp.text, "lxml")

        product_cards = soup.select(
            ".product-card, .ProductCard, [data-testid='product-card'], "
            "a[href*='/p/']"
        )

        for card in product_cards:
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

        title_el = card.select_one(
            ".product-name, .ProductCard__name, h2, h3, "
            "[data-testid='product-name']"
        )
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        if not title:
            return None

        price_el = card.select_one(
            ".product-price, .ProductCard__price, [data-testid='product-price']"
        )
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price = self.parse_brl_price(raw_price)

        img_el = card.select_one("img[src]")
        image_url = img_el["src"] if img_el else ""

        return Listing(
            source="enjoei",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="used",
        )
