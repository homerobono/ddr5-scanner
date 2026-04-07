"""DDR5 CL30 Brazilian Deal Scanner - main orchestrator."""

from __future__ import annotations

import asyncio
import sys
import time

import yaml
from dotenv import load_dotenv

from db.history import HistoryDB
from llm.classifier import OllamaClassifier
from notifications.email_notifier import EmailNotifier
from scrapers.base import Listing, ScraperResult
from utils.logging import get_logger, setup_logging

SCRAPER_REGISTRY: dict[str, type] = {}


def _register_scrapers() -> None:
    from scrapers.aliexpress import AliExpressScraper
    from scrapers.amazon import AmazonScraper
    from scrapers.enjoei import EnjoeiScraper
    from scrapers.facebook import FacebookScraper
    from scrapers.google_shopping import GoogleShoppingScraper
    from scrapers.kabum import KabumScraper
    from scrapers.mercadolivre import MercadoLivreScraper
    from scrapers.olx import OLXScraper
    from scrapers.pichau import PichauScraper
    from scrapers.terabyte import TerabyteScraper

    SCRAPER_REGISTRY.update(
        {
            "kabum": KabumScraper,
            "pichau": PichauScraper,
            "terabyte": TerabyteScraper,
            "mercadolivre": MercadoLivreScraper,
            "enjoei": EnjoeiScraper,
            "facebook": FacebookScraper,
            "amazon": AmazonScraper,
            "olx": OLXScraper,
            "aliexpress": AliExpressScraper,
            "google_shopping": GoogleShoppingScraper,
        }
    )


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


async def run_scraper(scraper_cls: type, config: dict) -> ScraperResult:
    log = get_logger("main")
    name = scraper_cls.__name__
    start = time.monotonic()
    try:
        scraper = scraper_cls(config)
        listings = await scraper.search()
        elapsed = time.monotonic() - start
        log.info(f"{name}: found {len(listings)} raw listings ({elapsed:.1f}s)")
        return ScraperResult(source=name, listings=listings, error=None)
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.error(f"{name}: FAILED after {elapsed:.1f}s - {exc}")
        return ScraperResult(source=name, listings=[], error=str(exc))


async def main() -> None:
    load_dotenv()
    log = setup_logging()
    config = load_config()
    _register_scrapers()

    enabled = config.get("scrapers", {}).get("enabled", [])
    scrapers_to_run = [
        SCRAPER_REGISTRY[name] for name in enabled if name in SCRAPER_REGISTRY
    ]

    if not scrapers_to_run:
        log.error("No scrapers enabled in config.yaml")
        sys.exit(1)

    log.info(f"Starting scan with {len(scrapers_to_run)} scrapers...")

    results: list[ScraperResult] = await asyncio.gather(
        *(run_scraper(cls, config) for cls in scrapers_to_run)
    )

    all_listings: list[Listing] = []
    scraper_status: dict[str, str] = {}
    for result in results:
        scraper_status[result.source] = result.error or "OK"
        all_listings.extend(result.listings)

    log.info(f"Total raw listings collected: {len(all_listings)}")

    db = HistoryDB(config.get("database", {}).get("path", "data/scanner.db"))
    try:
        new_listings = db.filter_new_or_price_dropped(all_listings)
        log.info(f"New or price-dropped listings: {len(new_listings)}")

        db.save_listings(all_listings)

        if not new_listings:
            log.info("No new listings to classify. Done.")
            _print_status(log, scraper_status)
            return

        classifier = OllamaClassifier(config)
        classified = await classifier.classify_and_extract(new_listings)
        log.info(f"Classified listings: {len(classified)}")

        threshold = config.get("price_threshold_brl", 600.0)
        min_cap = config.get("min_capacity_gb", 16)
        matches = [
            item
            for item in classified
            if item.is_match
            and item.confidence >= 0.6
            and item.listing.price is not None
            and item.listing.price <= threshold
            and (item.capacity_gb or 0) >= min_cap
        ]
        log.info(f"Matches below R${threshold:.2f}: {len(matches)}")

        if matches:
            notifier = EmailNotifier(config)
            notifier.send(matches, scraper_status)
            log.info("Email notification sent.")
        else:
            log.info("No matches found below threshold. No email sent.")

        _print_status(log, scraper_status)
    finally:
        db.close()


def _print_status(log, scraper_status: dict[str, str]) -> None:
    log.info("Scraper status summary:")
    for source, status in scraper_status.items():
        log.info(f"  {source}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
