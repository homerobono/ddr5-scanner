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
{"is_match": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}

Title: {title}
Description: {description}
Price: {raw_price}"""

EXTRACTION_PROMPT = """\
Extract structured product details from this DDR5 memory listing.
Reply ONLY with a JSON object (no other text):
{{"brand": "string", "model": "string", "capacity_gb": integer, \
"speed_mhz": integer_or_null, "cas_latency": integer_or_null, \
"kit_count": integer, "condition": "new|used"}}

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
        results: list[ClassifiedListing] = []
        sem = asyncio.Semaphore(3)

        async def process(listing: Listing) -> ClassifiedListing:
            async with sem:
                return await self._process_single(listing)

        tasks = [process(listing) for listing in listings]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        classified = []
        for r in results:
            if isinstance(r, Exception):
                self.log.warning(f"Classification failed: {r}")
            else:
                classified.append(r)

        return classified

    async def _process_single(self, listing: Listing) -> ClassifiedListing:
        classification = await self._classify(listing)

        result = ClassifiedListing(
            listing=listing,
            is_match=classification.get("is_match", False),
            confidence=classification.get("confidence", 0.0),
            reason=classification.get("reason", ""),
        )

        if result.is_match and result.confidence >= 0.5:
            extraction = await self._extract(listing)
            result.brand = extraction.get("brand", "")
            result.model = extraction.get("model", "")
            result.capacity_gb = extraction.get("capacity_gb")
            result.speed_mhz = extraction.get("speed_mhz")
            result.cas_latency = extraction.get("cas_latency")
            result.kit_count = extraction.get("kit_count", 1)
            if extraction.get("condition"):
                result.listing.condition = extraction["condition"]

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

        # Try direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code blocks or surrounding text
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Try extracting from ```json blocks
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        self.log.debug(f"Could not parse JSON from LLM response: {text[:200]}")
        return {}
