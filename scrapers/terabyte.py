"""Terabyte Shop scraper using Selenium for JS rendering."""

from __future__ import annotations

import asyncio
from functools import partial
from urllib.parse import quote_plus

from scrapers.base import BaseScraper, Listing


class TerabyteScraper(BaseScraper):
    name = "terabyte"
    BASE_URL = "https://www.terabyteshop.com.br"

    async def search(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_urls: set[str] = set()

        for query in self.search_queries:
            try:
                results = await asyncio.get_event_loop().run_in_executor(
                    None, partial(self._search_sync, query)
                )
                for listing in results:
                    if listing.url not in seen_urls:
                        seen_urls.add(listing.url)
                        listings.append(listing)
            except Exception as exc:
                self.log.warning(f"Query '{query}' failed: {exc}")
            await self.throttle()

        return listings

    def _search_sync(self, query: str) -> list[Listing]:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument(f"user-agent={self.random_ua()}")
        options.add_argument("--lang=pt-BR")

        driver = webdriver.Chrome(options=options)
        listings: list[Listing] = []

        try:
            url = f"{self.BASE_URL}/busca?str={quote_plus(query)}"
            driver.get(url)

            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".pbox"))
            )

            cards = driver.find_elements(By.CSS_SELECTOR, ".pbox")

            for card in cards:
                try:
                    listing = self._parse_card_selenium(card)
                    if listing:
                        listings.append(listing)
                except Exception as exc:
                    self.log.debug(f"Failed to parse card: {exc}")

        finally:
            driver.quit()

        return listings

    def _parse_card_selenium(self, card) -> Listing | None:
        from selenium.webdriver.common.by import By

        try:
            link_el = card.find_element(By.CSS_SELECTOR, "a[href]")
            href = link_el.get_attribute("href") or ""
        except Exception:
            return None

        try:
            title_el = card.find_element(By.CSS_SELECTOR, "h2.prod-name, .prod-name, h2")
            title = title_el.text.strip()
        except Exception:
            title = ""

        if not title:
            return None

        try:
            price_el = card.find_element(By.CSS_SELECTOR, ".prod-new-price, .val-prod, .prod-price")
            raw_price = price_el.text.strip()
        except Exception:
            raw_price = ""

        price = self.parse_brl_price(raw_price)

        try:
            img_el = card.find_element(By.CSS_SELECTOR, "img[src]")
            image_url = img_el.get_attribute("src") or ""
        except Exception:
            image_url = ""

        return Listing(
            source="terabyte",
            title=title,
            url=href,
            price=price,
            raw_price=raw_price,
            image_url=image_url,
            condition="new",
        )
