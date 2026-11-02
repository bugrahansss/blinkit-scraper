"""Fetch Blinkit pages through the Scrape.do API.

Blinkit sits behind Cloudflare and answers scripts with HTTP 403. Through
Scrape.do every request uses residential proxies in India (``super=true`` and
``geoCode=in``); the listing API only answers on those. Each request costs
10 Scrape.do credits.

Prices, assortment and stock belong to one dark store, chosen from a delivery
location. Pages take it from the ``gr_1_lat`` / ``gr_1_lon`` cookies, the
listing API from ``lat`` / ``lon`` headers. Without a location Blinkit uses
Gurugram.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, Optional

import requests

from .parser import BLINKIT, extract_state, category_ids, parse_category, parse_listing_api, parse_product, product_id_from_url

API_URL = "https://api.scrape.do/"
TOKEN_ENV = "SCRAPEDO_TOKEN"
DEFAULT_LAT, DEFAULT_LON = 28.413333, 77.072833  # Gurugram, Blinkit's own default

CITIES = {
    "gurugram": (28.413333, 77.072833),
    "delhi": (28.6315, 77.2167),
    "mumbai": (19.0760, 72.8777),
    "bengaluru": (12.9716, 77.5946),
    "hyderabad": (17.3850, 78.4867),
    "pune": (18.5204, 73.8567),
    "kolkata": (22.5726, 88.3639),
    "chennai": (13.0827, 80.2707),
}


class ScrapeError(RuntimeError):
    """A request that did not return usable data. ``from_target`` is True when the status came from Blinkit."""

    def __init__(self, message: str, status_code: Optional[int] = None, from_target: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.from_target = from_target


def _is_json(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


class BlinkitScraper:
    """Blinkit category listings and product pages, for one delivery location.

    >>> scraper = BlinkitScraper(token="...", city="mumbai")
    >>> rows = scraper.get_category("https://blinkit.com/cn/chips-crisps/cid/1237/940", max_pages=3)
    """

    def __init__(
        self,
        token: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        city: Optional[str] = None,
        timeout: float = 90,
        retries: int = 2,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.token = (token or os.getenv(TOKEN_ENV) or "").strip()
        if not self.token:
            raise ValueError(f"A Scrape.do API token is required (pass token= or set {TOKEN_ENV}).")
        if city:
            key = city.strip().lower()
            if key not in CITIES:
                raise ValueError(f"Unknown city {city!r}. Known: {', '.join(sorted(CITIES))}. Or pass lat and lon.")
            lat, lon = CITIES[key]
        self.lat = float(lat) if lat is not None else DEFAULT_LAT
        self.lon = float(lon) if lon is not None else DEFAULT_LON
        self.timeout = timeout
        self.retries = max(0, retries)
        self.session = session or requests.Session()
        self.credits_used = 0

    # ------------------------------------------------------------ transport

    def request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[str] = None,
        valid: Optional[Callable[[str], bool]] = None,
    ) -> str:
        params: Dict[str, str] = {"token": self.token, "url": url, "super": "true", "geoCode": "in"}
        send = None
        if headers:
            params["extraHeaders"] = "true"
            send = {f"sd-{k}": v for k, v in headers.items()}
        if cookies:
            params["setCookies"] = cookies
        last: Optional[ScrapeError] = None
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(1.5 * attempt)
            try:
                response = self.session.request(
                    method, API_URL, params=params, headers=send, data="{}" if method == "POST" else None, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last = ScrapeError(f"Request failed: {str(exc).replace(self.token, '***')}")
                continue
            from_target = "scrape.do-initial-status-code" in {k.lower() for k in response.headers}
            try:
                self.credits_used += int(response.headers.get("scrape.do-request-cost") or 0)
            except ValueError:
                pass
            if response.status_code == 401 and not from_target:
                raise ScrapeError("Scrape.do rejected the token (HTTP 401).", 401, False)
            if response.status_code == 200 and response.text and (valid is None or valid(response.text)):
                return response.text
            last = ScrapeError(
                f"HTTP {response.status_code} for {url}" if response.status_code != 200 else f"Page without Blinkit data: {url}",
                response.status_code, from_target,
            )
            if response.status_code not in (200, 429, 500, 502, 503, 504):
                break
        raise last or ScrapeError(f"Request failed: {url}")

    def _cookies(self) -> str:
        return f"gr_1_lat={self.lat}; gr_1_lon={self.lon}"

    def fetch_page(self, url: str) -> str:
        """A Blinkit page for this location (category or product)."""
        return self.request(url, cookies=self._cookies(), valid=lambda body: bool(extract_state(body)))

    def fetch_listing_api(self, next_url: str) -> Dict[str, Any]:
        """One page of /v1/layout/listing_widgets (15 products) for this location."""
        headers = {
            "lat": str(self.lat), "lon": str(self.lon), "app_client": "consumer_web",
            "Content-Type": "application/json", "Accept": "application/json",
        }
        url = next_url if next_url.startswith("http") else BLINKIT + next_url
        return json.loads(self.request(url, method="POST", headers=headers, valid=_is_json))

    # ------------------------------------------------------------- scraping

    def get_category(self, url: str, max_pages: int = 1, with_info: bool = False) -> Any:
        """Products of a category page. Page 1 is the HTML (30 products); every further page is one
        listing-API call (15 products). Out-of-stock products are kept with ``in_stock=False``."""
        if not category_ids(url):
            raise ValueError("Expected a Blinkit category URL like https://blinkit.com/cn/chips-crisps/cid/1237/940")
        first = parse_category(self.fetch_page(url))
        rows: Dict[int, Dict[str, Any]] = {row["product_id"]: row for row in first["products"]}
        next_url, pages = first["next_url"], 1
        while next_url and pages < max(1, max_pages):
            batch = parse_listing_api(self.fetch_listing_api(next_url))
            pages += 1
            for row in batch["products"]:
                rows.setdefault(row["product_id"], row)
            if not batch["products"]:
                break
            next_url = batch["next_url"]
        products = list(rows.values())
        if not with_info:
            return products
        return {
            "products": products, "total": first["total"], "pages_read": pages, "has_more": bool(next_url),
            "locality": first["locality"], "lat": self.lat, "lon": self.lon,
            "merchant_id": next((r["merchant_id"] for r in products if r.get("merchant_id")), None),
        }

    def get_product(self, url_or_id: Any) -> Optional[Dict[str, Any]]:
        """Product page: price, MRP, stock and the attribute sheet (FSSAI licence, shelf life...)."""
        text = str(url_or_id).strip()
        product_id = int(text) if text.isdigit() else product_id_from_url(text)
        if not product_id:
            raise ValueError("Expected a Blinkit product URL (.../prn/<name>/prid/<id>) or a product id.")
        return parse_product(self.fetch_page(f"{BLINKIT}/prn/product/prid/{product_id}"))
