"""Kabum scraper using their public catalog API."""

from __future__ import annotations

import httpx

from scrapers.base import BaseScraper, Listing


class KabumScraper(BaseScraper):
    name = "kabum"
    API_BASE = "https://servicespub.prod.api.aws.grupokabum.com.br"
    SEARCH_URL = f"{API_BASE}/catalog/v2/products"

    async def search(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
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
        page = 1
        max_pages = 5

        while page <= max_pages:
            params = {
                "query": query,
                "page_number": str(page),
                "page_size": "100",
                "sort": "most_searched",
            }
            headers = {
                "User-Agent": self.random_ua(),
                "Accept": "application/json",
                "Origin": "https://www.kabum.com.br",
                "Referer": "https://www.kabum.com.br/",
            }

            resp = await self._request_with_retry(
                client, "GET", self.SEARCH_URL, params=params, headers=headers
            )
            data = resp.json()

            products = data.get("data", [])
            if not products:
                break

            for p in products:
                url = f"https://www.kabum.com.br/produto/{p.get('code', '')}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                price = p.get("price") or p.get("priceWithDiscount")
                old_price = p.get("oldPrice")

                listings.append(
                    Listing(
                        source="kabum",
                        title=p.get("name", ""),
                        url=url,
                        price=float(price) if price else None,
                        raw_price=f"R$ {price}" if price else "",
                        description=p.get("description", ""),
                        image_url=p.get("image", ""),
                        seller=p.get("manufacturer", {}).get("name", ""),
                        condition="new",
                        extra={
                            "old_price": old_price,
                            "available": p.get("available", False),
                            "rating": p.get("rating"),
                        },
                    )
                )

            total_pages = data.get("meta", {}).get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
            await self.throttle()
