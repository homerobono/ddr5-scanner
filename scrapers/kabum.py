"""Kabum scraper using their public catalog API with search + category fallback."""

from __future__ import annotations

import httpx

from scrapers.base import BaseScraper, Listing


class KabumScraper(BaseScraper):
    name = "kabum"
    API_BASE = "https://servicespub.prod.api.aws.grupokabum.com.br"
    SEARCH_URL = f"{API_BASE}/catalog/v2/products"
    CATEGORY_URL = f"{API_BASE}/catalog/v2/products-by-category"

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

            if not listings:
                self.log.info("Search API returned nothing, trying category endpoint")
                try:
                    await self._search_category(client, listings, seen_urls)
                except Exception as exc:
                    self.log.warning(f"Category fallback failed: {exc}")

        return listings

    def _api_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.random_ua(),
            "Accept": "application/json",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://www.kabum.com.br",
            "Referer": "https://www.kabum.com.br/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }

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

            resp = await self._request_with_retry(
                client, "GET", self.SEARCH_URL,
                params=params, headers=self._api_headers(),
            )
            data = resp.json()

            products = data.get("data", [])
            if not products:
                break

            self._collect_products(products, listings, seen_urls)

            total_pages = data.get("meta", {}).get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
            await self.throttle()

    async def _search_category(
        self,
        client: httpx.AsyncClient,
        listings: list[Listing],
        seen_urls: set[str],
    ) -> None:
        """Fetch DDR5 memory from the hardware/memorias category."""
        categories = ["hardware/memorias", "hardware"]

        for category in categories:
            url = f"{self.CATEGORY_URL}/{category}"
            params = {
                "page_number": "1",
                "page_size": "100",
                "facet_filters": "",
                "sort": "most_searched",
                "is_prime": "false",
                "payload_data": "products_category_filters",
                "include": "gift",
            }
            try:
                resp = await self._request_with_retry(
                    client, "GET", url,
                    params=params, headers=self._api_headers(),
                )
                data = resp.json()
                products = data.get("data", [])
                if products:
                    self._collect_products(products, listings, seen_urls)
                    self.log.info(
                        f"Category '{category}': found {len(products)} products"
                    )
            except Exception as exc:
                self.log.debug(f"Category '{category}' failed: {exc}")
            await self.throttle()

    def _collect_products(
        self,
        products: list[dict],
        listings: list[Listing],
        seen_urls: set[str],
    ) -> None:
        for p in products:
            code = p.get("code", "")
            url = f"https://www.kabum.com.br/produto/{code}"
            if url in seen_urls:
                continue
            seen_urls.add(url)

            price = p.get("priceWithDiscount") or p.get("price")
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
                    seller=p.get("manufacturer", {}).get("name", "")
                    if isinstance(p.get("manufacturer"), dict)
                    else "",
                    condition="new",
                    extra={
                        "old_price": old_price,
                        "available": p.get("available", False),
                        "rating": p.get("rating"),
                    },
                )
            )
