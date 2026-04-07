"""Amazon BR scraper using HTTP requests + BeautifulSoup with UA rotation."""

from __future__ import annotations

from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class AmazonScraper(BaseScraper):
    name = "amazon"
    BASE_URL = "https://www.amazon.com.br"

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
            url = f"{self.BASE_URL}/s?k={quote_plus(query)}&page={page_num}"
            headers = self.default_headers()
            headers["Accept-Encoding"] = "gzip, deflate, br"

            resp = await self._request_with_retry(
                client, "GET", url, headers=headers
            )
            soup = BeautifulSoup(resp.text, "lxml")

            cards = soup.select('[data-component-type="s-search-result"]')
            if not cards:
                break

            for card in cards:
                try:
                    listing = self._parse_card(card)
                    if listing and listing.url not in seen_urls:
                        seen_urls.add(listing.url)
                        listings.append(listing)
                except Exception as exc:
                    self.log.debug(f"Failed to parse card: {exc}")

            await self.throttle()

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        title_el = card.select_one(
            "h2 a span, h2 span.a-text-normal"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        link_el = card.select_one("h2 a[href]")
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
            price_el = card.select_one(".a-price, .a-offscreen")
            raw_price = price_el.get_text(strip=True) if price_el else ""
            price = self.parse_brl_price(raw_price)

        img_el = card.select_one("img.s-image")
        image_url = img_el["src"] if img_el else ""

        seller_el = card.select_one(".a-size-small .a-color-secondary")
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
