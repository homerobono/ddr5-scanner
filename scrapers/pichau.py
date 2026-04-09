"""Pichau scraper – tries the Magento GraphQL API first, Playwright as fallback."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing

GRAPHQL_QUERY = """
query ProductSearch($search: String!, $pageSize: Int!, $currentPage: Int!) {
  products(search: $search, pageSize: $pageSize, currentPage: $currentPage) {
    total_count
    items {
      name
      sku
      url_key
      price_range {
        minimum_price {
          final_price { value currency }
          regular_price { value currency }
        }
      }
      small_image { url label }
    }
  }
}
"""


class PichauScraper(BaseScraper):
    name = "pichau"
    BASE_URL = "https://www.pichau.com.br"
    GRAPHQL_URL = f"{BASE_URL}/graphql"

    async def search(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_urls: set[str] = set()

        listings = await self._search_graphql(seen_urls)
        if listings:
            return listings

        self.log.info("GraphQL returned nothing, falling back to Playwright")
        return await self._search_playwright(seen_urls)

    async def _search_graphql(self, seen_urls: set[str]) -> list[Listing]:
        listings: list[Listing] = []
        headers = {
            "User-Agent": self.random_ua(),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/",
            "Store": "pichau",
        }

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for query in self.search_queries:
                try:
                    payload = {
                        "query": GRAPHQL_QUERY,
                        "variables": {
                            "search": query,
                            "pageSize": 48,
                            "currentPage": 1,
                        },
                    }
                    resp = await self._request_with_retry(
                        client,
                        "POST",
                        self.GRAPHQL_URL,
                        json=payload,
                        headers=headers,
                    )
                    data = resp.json()
                    items = (
                        data.get("data", {})
                        .get("products", {})
                        .get("items", [])
                    )
                    for item in items:
                        listing = self._parse_graphql_item(item)
                        if listing and listing.url not in seen_urls:
                            seen_urls.add(listing.url)
                            listings.append(listing)
                except Exception as exc:
                    self.log.debug(f"GraphQL query '{query}' failed: {exc}")
                await self.throttle()

        return listings

    def _parse_graphql_item(self, item: dict) -> Listing | None:
        name = item.get("name", "")
        if not name:
            return None

        url_key = item.get("url_key", "")
        url = f"{self.BASE_URL}/{url_key}" if url_key else ""
        if not url:
            return None

        price_data = (
            item.get("price_range", {})
            .get("minimum_price", {})
            .get("final_price", {})
        )
        price = price_data.get("value")
        raw_price = f"R$ {price:,.2f}" if price else ""

        image = item.get("small_image", {})
        image_url = image.get("url", "") if image else ""

        return Listing(
            source="pichau",
            title=name,
            url=url,
            price=float(price) if price else None,
            raw_price=raw_price,
            image_url=image_url,
            condition="new",
            extra={"sku": item.get("sku", "")},
        )

    async def _search_playwright(self, seen_urls: set[str]) -> list[Listing]:
        listings: list[Listing] = []

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

            for query in self.search_queries:
                try:
                    await self._search_query_pw(page, query, listings, seen_urls)
                except Exception as exc:
                    self.log.warning(f"Query '{query}' failed: {exc}")
                await self.throttle()

            await browser.close()

        return listings

    async def _search_query_pw(
        self,
        page,
        query: str,
        listings: list[Listing],
        seen_urls: set[str],
    ) -> None:
        url = f"{self.BASE_URL}/catalogsearch/result/?q={quote_plus(query)}"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if resp and resp.status == 403:
            self.log.warning(f"Got 403 for '{query}', waiting before retry...")
            await asyncio.sleep(8)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp and resp.status == 403:
                self.log.warning(f"Still 403 for '{query}', skipping")
                return

        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        await asyncio.sleep(3)

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

        html = await page.content()
        self._dump_debug_html(html, query)

        # Try intercepting __NEXT_DATA__ or similar JSON payloads
        listings_from_json = self._extract_from_next_data(html, seen_urls)
        if listings_from_json:
            listings.extend(listings_from_json)
            return

        soup = BeautifulSoup(html, "lxml")

        product_cards = soup.select(
            '[data-testid="product-card"], .product-card, '
            ".product-item, .product-grid-item, "
            "[class*='ProductCard'], [class*='product-card']"
        )

        if not product_cards:
            product_cards = soup.select(
                "a[href*='/produto/'], a[href*='/product/'], "
                "a[href*='.html']"
            )

        for card in product_cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as exc:
                self.log.debug(f"Failed to parse card: {exc}")

    def _extract_from_next_data(
        self, html: str, seen_urls: set[str]
    ) -> list[Listing]:
        """Try to extract product data from embedded JSON (Next.js / inline scripts)."""
        listings: list[Listing] = []
        soup = BeautifulSoup(html, "lxml")
        for script in soup.select("script[id='__NEXT_DATA__'], script[type='application/json']"):
            try:
                data = json.loads(script.string or "")
                products = self._find_products_in_json(data)
                for p in products:
                    name = p.get("name", "")
                    url_key = p.get("url_key") or p.get("urlKey") or p.get("slug", "")
                    url = f"{self.BASE_URL}/{url_key}" if url_key else ""
                    if not name or not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    price = None
                    for price_key in ("final_price", "price", "specialPrice", "finalPrice"):
                        val = p.get(price_key)
                        if isinstance(val, dict):
                            val = val.get("value")
                        if val:
                            price = float(val)
                            break

                    listings.append(
                        Listing(
                            source="pichau",
                            title=name,
                            url=url,
                            price=price,
                            raw_price=f"R$ {price:,.2f}" if price else "",
                            image_url=p.get("image", {}).get("url", "") if isinstance(p.get("image"), dict) else str(p.get("image", "")),
                            condition="new",
                        )
                    )
            except (json.JSONDecodeError, TypeError):
                continue
        return listings

    def _find_products_in_json(self, obj, depth: int = 0) -> list[dict]:
        """Recursively search JSON for product-like objects."""
        if depth > 8:
            return []
        results: list[dict] = []
        if isinstance(obj, dict):
            if "name" in obj and ("url_key" in obj or "urlKey" in obj or "sku" in obj):
                results.append(obj)
            for v in obj.values():
                results.extend(self._find_products_in_json(v, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(self._find_products_in_json(item, depth + 1))
        return results

    def _parse_card(self, card: BeautifulSoup) -> Listing | None:
        link = card.find("a", href=True) if card.name != "a" else card
        if not link or not link.get("href"):
            return None

        href = link["href"]
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        title_el = card.select_one(
            "h2, h3, .product-name, [data-testid='product-name'], "
            "[class*='ProductName'], [class*='product-name']"
        )
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        if not title:
            return None

        price_el = card.select_one(
            ".price, .product-price, [data-testid='product-price'], "
            ".boleto span, .valorcartao, [class*='Price'], [class*='price']"
        )
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price = self.parse_brl_price(raw_price)

        if not price:
            for el in card.select("span, div, p"):
                text = el.get_text(strip=True)
                if "R$" in text:
                    price = self.parse_brl_price(text)
                    if price:
                        raw_price = text
                        break

        img_el = card.select_one("img[src], img[data-src]")
        image_url = ""
        if img_el:
            image_url = img_el.get("src") or img_el.get("data-src") or ""

        return Listing(
            source="pichau",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="new",
        )
