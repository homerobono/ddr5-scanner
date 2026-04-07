"""AliExpress scraper using HTTP requests + BeautifulSoup."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing


class AliExpressScraper(BaseScraper):
    name = "aliexpress"
    BASE_URL = "https://pt.aliexpress.com"

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
        url = f"{self.BASE_URL}/w/wholesale-{quote_plus(query)}.html"
        params = {
            "SearchText": query,
            "shipToCountry": "BR",
        }
        headers = self.default_headers()
        headers["Accept-Language"] = "pt-BR,pt;q=0.9"

        resp = await self._request_with_retry(
            client, "GET", url, params=params, headers=headers
        )
        soup = BeautifulSoup(resp.text, "lxml")

        cards = soup.select(
            ".search-item-card-wrapper-gallery, "
            "[class*='SearchItem'], "
            "a[href*='/item/']"
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
        link = card if card.name == "a" else card.find("a", href=True)
        if not link or not link.get("href"):
            return None

        href = link["href"]
        if href.startswith("//"):
            href = f"https:{href}"
        elif not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        title_el = card.select_one(
            "h1, h3, .multi--titleText--nXeOvyr, [class*='title']"
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            title = link.get_text(strip=True)
        if not title:
            return None

        price_el = card.select_one(
            "[class*='price'], .multi--price--1okBCly, .search-price"
        )
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price = self._parse_ali_price(raw_price)

        img_el = card.select_one("img[src]")
        image_url = img_el["src"] if img_el else ""
        if image_url and image_url.startswith("//"):
            image_url = f"https:{image_url}"

        return Listing(
            source="aliexpress",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="new",
        )

    def _parse_ali_price(self, text: str) -> float | None:
        if not text:
            return None
        match = re.search(r"R\$\s*([\d.,]+)", text)
        if match:
            return self.parse_brl_price(match.group(0))
        cleaned = re.sub(r"[^\d.,]", "", text)
        if cleaned:
            return self.parse_brl_price(cleaned)
        return None
