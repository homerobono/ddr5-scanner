"""Google Shopping scraper as a broad-net fallback."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class GoogleShoppingScraper(BaseScraper):
    name = "google_shopping"
    SEARCH_URL = "https://www.google.com.br/search"

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
        params = {
            "tbm": "shop",
            "q": query,
            "gl": "br",
            "hl": "pt-BR",
        }
        headers = self.default_headers()
        headers["Referer"] = "https://www.google.com.br/"

        resp = await self._request_with_retry(
            client, "GET", self.SEARCH_URL, params=params, headers=headers
        )
        soup = BeautifulSoup(resp.text, "lxml")

        cards = soup.select(".sh-dgr__gr-auto, .sh-dlr__list-result, .sh-pr__product-results-grid div[data-docid]")

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
                from urllib.parse import unquote
                href = unquote(match.group(1))
        if not href.startswith("http"):
            href = f"https://www.google.com.br{href}"

        title_el = card.select_one("h3, h4, .tAxDx, [data-name]")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            title = link.get_text(strip=True)
        if not title:
            return None

        price_el = card.select_one(
            ".a8Pemb, .HRLxBb, [data-price], span:has(> b)"
        )
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price = self.parse_brl_price(raw_price)

        seller_el = card.select_one(".aULzUe, .IuHnof, .E5ocAb")
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
