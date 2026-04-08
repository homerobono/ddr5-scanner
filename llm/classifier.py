"""Ollama-based LLM classifier and structured data extractor."""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from scrapers.base import ClassifiedListing, Listing
from utils.logging import get_logger

CLASSIFICATION_PROMPT = """\
You are a hardware product classifier. Analyze this product listing and determine \
if it is a DDR5 memory module (RAM) with CAS Latency 30 (CL30) and total capacity \
of 16GB or more.

Common model number patterns:
- "CL30" or "C30" in the name means CAS Latency 30
- Kingston FURY Beast: KF560C30 = DDR5 6000MHz CL30, KF548C30 = DDR5 4800MHz CL30
- G.Skill Trident Z5: F5-6000J3038F16G = DDR5 6000 CL30
- Corsair Vengeance: CMK32GX5M2B6000C30 = DDR5 6000 CL30

Reply ONLY with a JSON object (no other text):
{{"is_match": true, "confidence": 0.95, "reason": "brief explanation"}}

Title: {title}
Description: {description}
Price: {raw_price}"""

EXTRACTION_PROMPT = """\
Extract structured product details from this DDR5 memory listing.
Reply ONLY with a JSON object (no other text):
{{"brand": "string", "model": "string", "capacity_gb": 16, \
"speed_mhz": 6000, "cas_latency": 30, \
"kit_count": 1, "condition": "new"}}

Title: {title}
Description: {description}
Price: {raw_price}"""


class OllamaClassifier:
    def __init__(self, config: dict) -> None:
        ollama_cfg = config.get("ollama", {})
        self.base_url = ollama_cfg.get("base_url", "http://localhost:11434")
        self.model = ollama_cfg.get("model", "llama3")
        self.timeout = ollama_cfg.get("timeout", 60)
        self.log = get_logger("llm.classifier")

    async def classify_and_extract(
        self, listings: list[Listing]
    ) -> list[ClassifiedListing]:
        total = len(listings)
        self.log.info(f"Starting LLM classification of {total} listings...")
        sem = asyncio.Semaphore(3)
        completed = 0
        matches = 0
        errors = 0

        async def process(listing: Listing) -> ClassifiedListing | None:
            nonlocal completed, matches, errors
            async with sem:
                result = await self._process_single(listing)
                completed += 1
                if result is None:
                    errors += 1
                elif result.is_match:
                    matches += 1
                self.log.info(
                    f"[{completed}/{total}] "
                    f"matches={matches} errors={errors} | "
                    f"{listing.title[:60]}"
                )
                return result

        tasks = [process(listing) for listing in listings]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        classified = []
        for r in results:
            if isinstance(r, Exception):
                self.log.warning(f"Classification failed: {r}")
            elif r is not None:
                classified.append(r)

        self.log.info(
            f"Classification complete: {len(classified)} classified, "
            f"{matches} matches, {errors} errors out of {total}"
        )
        return classified

    async def _process_single(self, listing: Listing) -> ClassifiedListing | None:
        try:
            classification = await self._classify(listing)
        except Exception as exc:
            self.log.debug(f"Classification error for '{listing.title[:50]}': {exc}")
            return None

        is_match = classification.get("is_match", False)
        if isinstance(is_match, str):
            is_match = is_match.lower() in ("true", "yes", "1")

        confidence = classification.get("confidence", 0.0)
        if isinstance(confidence, str):
            try:
                confidence = float(confidence)
            except ValueError:
                confidence = 0.0

        result = ClassifiedListing(
            listing=listing,
            is_match=bool(is_match),
            confidence=confidence,
            reason=str(classification.get("reason", "")),
        )

        if result.is_match and result.confidence >= 0.5:
            try:
                extraction = await self._extract(listing)
                result.brand = str(extraction.get("brand", ""))
                result.model = str(extraction.get("model", ""))
                result.capacity_gb = extraction.get("capacity_gb")
                result.speed_mhz = extraction.get("speed_mhz")
                result.cas_latency = extraction.get("cas_latency")
                result.kit_count = extraction.get("kit_count", 1)
                if extraction.get("condition"):
                    result.listing.condition = extraction["condition"]
            except Exception as exc:
                self.log.debug(f"Extraction error for '{listing.title[:50]}': {exc}")

        return result

    async def _classify(self, listing: Listing) -> dict:
        prompt = CLASSIFICATION_PROMPT.format(
            title=listing.title,
            description=listing.description[:500],
            raw_price=listing.raw_price,
        )
        return await self._query_ollama(prompt)

    async def _extract(self, listing: Listing) -> dict:
        prompt = EXTRACTION_PROMPT.format(
            title=listing.title,
            description=listing.description[:500],
            raw_price=listing.raw_price,
        )
        return await self._query_ollama(prompt)

    async def _query_ollama(self, prompt: str) -> dict:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": 256,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()

            response_text = data.get("response", "")
            return self._parse_json_response(response_text)

        except httpx.TimeoutException:
            self.log.warning("Ollama request timed out")
            return {}
        except httpx.HTTPError as exc:
            self.log.warning(f"Ollama HTTP error: {exc}")
            return {}
        except Exception as exc:
            self.log.warning(f"Ollama error: {exc}")
            return {}

    def _parse_json_response(self, text: str) -> dict:
        text = text.strip()
        if not text:
            return {}

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Try extracting JSON object from surrounding text
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Try extracting from markdown code blocks
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        self.log.debug(f"Could not parse JSON from LLM response: {text[:200]}")
        return {}
