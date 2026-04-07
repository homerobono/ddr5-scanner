"""Mercado Livre scraper using the official MercadoLibre API."""

from __future__ import annotations

import os

import httpx

from scrapers.base import BaseScraper, Listing


class MercadoLivreScraper(BaseScraper):
    name = "mercadolivre"
    API_BASE = "https://api.mercadolibre.com"
    SITE_ID = "MLB"

    async def search(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_urls: set[str] = set()
        token = os.environ.get("MELI_ACCESS_TOKEN", "")

        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for query in self.search_queries:
                try:
                    await self._search_query(
                        client, query, headers, listings, seen_urls
                    )
                except Exception as exc:
                    self.log.warning(f"Query '{query}' failed: {exc}")
                await self.throttle()

        return listings

    async def _search_query(
        self,
        client: httpx.AsyncClient,
        query: str,
        headers: dict[str, str],
        listings: list[Listing],
        seen_urls: set[str],
    ) -> None:
        offset = 0
        limit = 50
        max_results = 200

        while offset < max_results:
            url = f"{self.API_BASE}/sites/{self.SITE_ID}/search"
            params = {
                "q": query,
                "offset": str(offset),
                "limit": str(limit),
            }

            resp = await self._request_with_retry(
                client, "GET", url, params=params, headers=headers
            )
            data = resp.json()

            results = data.get("results", [])
            if not results:
                break

            for item in results:
                permalink = item.get("permalink", "")
                if permalink in seen_urls:
                    continue
                seen_urls.add(permalink)

                price = item.get("price")
                condition = item.get("condition", "new")
                thumbnail = item.get("thumbnail", "")

                seller_info = item.get("seller", {})
                seller_name = seller_info.get("nickname", "")

                attrs_text = ""
                for attr in item.get("attributes", []):
                    name = attr.get("name", "")
                    val = attr.get("value_name", "")
                    if val:
                        attrs_text += f"{name}: {val}; "

                listings.append(
                    Listing(
                        source="mercadolivre",
                        title=item.get("title", ""),
                        url=permalink,
                        price=float(price) if price else None,
                        raw_price=f"R$ {price}" if price else "",
                        description=attrs_text.strip(),
                        image_url=thumbnail,
                        seller=seller_name,
                        condition="used" if condition == "used" else "new",
                        extra={
                            "item_id": item.get("id"),
                            "category_id": item.get("category_id"),
                            "sold_quantity": item.get("sold_quantity"),
                        },
                    )
                )

            paging = data.get("paging", {})
            total = paging.get("total", 0)
            offset += limit
            if offset >= total:
                break
            await self.throttle()
