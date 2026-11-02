"""Parse Blinkit pages.

Every Blinkit page is rendered on the server with its data in one inline script,
``window.grofers.PRELOADED_STATE = {...}``. Category pages keep the first 30
product cards in ``ui.plpContainer.feedData.snippets`` plus a ``next_url`` for
the listing API; product pages keep the catalog sheet (price, MRP, stock,
attributes such as FSSAI licence and shelf life) in
``ui.pdp.bffPdp.bffData.tracking.le_meta.custom_data.seo``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

BLINKIT = "https://blinkit.com"
CARD_TYPE = "product_card_snippet_type_2"
_STATE_RE = re.compile(r"window\.grofers\.PRELOADED_STATE\s*=\s*")
_DECODER = json.JSONDecoder()

LISTING_FIELDS = [
    "product_id", "name", "brand", "pack_size", "price_inr", "mrp_inr", "discount_pct",
    "in_stock", "inventory", "rating", "rating_count", "variants", "merchant_id", "image", "url",
]
PRODUCT_FIELDS = [
    "product_id", "name", "brand", "pack_size", "price_inr", "mrp_inr", "discount_pct", "in_stock",
    "inventory", "shelf_life", "fssai_license", "country_of_origin", "category_ids", "merchant_id", "url",
]

# ------------------------------------------------------------------ helpers


def extract_state(html: str) -> Dict[str, Any]:
    """The PRELOADED_STATE object Blinkit renders into every page, or {}."""
    match = _STATE_RE.search(html or "")
    if not match:
        return {}
    try:
        data, _ = _DECODER.raw_decode(html, match.end())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(node: Any) -> Optional[str]:
    if isinstance(node, dict):
        value = node.get("text")
        return value.strip() if isinstance(value, str) else None
    return node.strip() if isinstance(node, str) else None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    number = float(match.group(0).replace(",", ""))
    return int(number) if number.is_integer() else number


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pct(price: Any, mrp: Any) -> int:
    if isinstance(price, (int, float)) and isinstance(mrp, (int, float)) and mrp > 0 and price <= mrp:
        return int((mrp - price) * 100 // mrp)  # Blinkit rounds discounts down
    return 0


_MULTIPLIERS = {"k": 1_000, "lac": 100_000, "lakh": 100_000, "l": 100_000, "cr": 10_000_000, "crore": 10_000_000}


def parse_count(text: Any) -> Optional[int]:
    """'7 lac' -> 700000, '1.2k' -> 1200, '(980)' -> 980."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return int(text)
    match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([a-zA-Z]*)", str(text or ""))
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    return int(round(number * _MULTIPLIERS.get(match.group(2).lower(), 1)))


def slugify(name: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return slug or "product"


def product_url(product_id: Any, name: Any = None) -> str:
    return f"{BLINKIT}/prn/{slugify(name)}/prid/{product_id}" if product_id else ""


def product_id_from_url(url: str) -> Optional[int]:
    match = re.search(r"/prid/(\d+)", url or "")
    return int(match.group(1)) if match else None


def category_ids(url: str) -> Optional[tuple]:
    """(l0, l1) from a /cn/<slug>/cid/<l0>/<l1> URL."""
    match = re.search(r"/cid/(\d+)(?:/(\d+))?", url or "")
    if not match:
        return None
    return int(match.group(1)), (int(match.group(2)) if match.group(2) else None)


# ------------------------------------------------------------- product cards


def parse_card(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One listing card -> flat row. Returns None for non-product snippets."""
    if not isinstance(data, dict):
        return None
    cart = ((data.get("atc_action") or {}).get("add_to_cart") or {}).get("cart_item") or {}
    product_id = _int(data.get("product_id") or (data.get("identity") or {}).get("id") or cart.get("product_id"))
    if not product_id:
        return None
    name = _text(data.get("display_name")) or _text(data.get("name")) or cart.get("product_name")
    price = cart.get("price") if isinstance(cart.get("price"), (int, float)) else _num(_text(data.get("normal_price")))
    mrp = cart.get("mrp") if isinstance(cart.get("mrp"), (int, float)) else _num(_text(data.get("mrp")))
    if mrp is None:
        mrp = price
    inventory = data.get("inventory", cart.get("inventory"))
    inventory = inventory if isinstance(inventory, int) else 0
    sold_out = bool(data.get("is_sold_out")) or data.get("product_state") == "out_of_stock" or inventory <= 0
    bar = (data.get("rating") or {}).get("bar") or {}
    rating = bar.get("value")
    return {
        "product_id": product_id,
        "name": name,
        "brand": _text(data.get("brand_name")) or cart.get("brand"),
        "pack_size": _text(data.get("variant")) or cart.get("unit"),
        "price_inr": price,
        "mrp_inr": mrp,
        "discount_pct": _pct(price, mrp),
        "in_stock": not sold_out,
        "inventory": max(inventory, 0),
        "rating": round(rating, 2) if isinstance(rating, (int, float)) else None,
        "rating_count": parse_count(_text(bar.get("title"))) if bar else None,
        "variants": len(data.get("variant_list") or []) or 1,
        "merchant_id": _int(data.get("merchant_id") or cart.get("merchant_id")),
        "image": (data.get("image") or {}).get("url") or cart.get("image_url"),
        "url": product_url(product_id, name),
    }


def parse_cards(snippets: List[Any]) -> List[Dict[str, Any]]:
    rows = []
    for snippet in snippets or []:
        if isinstance(snippet, dict) and snippet.get("widget_type") == CARD_TYPE:
            row = parse_card(snippet.get("data"))
            if row:
                rows.append(row)
    return rows


def _total(next_url: Optional[str]) -> Optional[int]:
    if not next_url:
        return None
    values = parse_qs(urlparse(next_url).query).get("total_pagination_items")
    return _int(values[0]) if values else None


def parse_category(html: str) -> Dict[str, Any]:
    """Category page -> {'products', 'next_url', 'total', 'locality', 'lat', 'lon'}."""
    state = extract_state(html)
    feed = (((state.get("ui") or {}).get("plpContainer") or {}).get("feedData")) or {}
    next_url = (feed.get("pagination") or {}).get("next_url")
    coords = (((state.get("data") or {}).get("location") or {}).get("coords")) or {}
    return {
        "products": parse_cards(feed.get("snippets") or []),
        "next_url": next_url,
        "total": _total(next_url),
        "locality": coords.get("locality"),
        "lat": coords.get("lat"),
        "lon": coords.get("lon"),
    }


def parse_listing_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Response of /v1/layout/listing_widgets -> {'products', 'next_url'}."""
    response = (payload or {}).get("response") or {}
    next_url = (response.get("pagination") or {}).get("next_url")
    return {"products": parse_cards(response.get("snippets") or []), "next_url": next_url, "total": _total(next_url)}


# -------------------------------------------------------------- product page

_ATTRIBUTE_KEYS = {
    "shelf_life": ("Shelf Life",),
    "fssai_license": ("FSSAI License", "FSSAI License Number"),
    "country_of_origin": ("Country of Origin",),
    "unit": ("Unit", "Net Weight"),
    "ingredients": ("Ingredients",),
    "manufacturer": ("Manufacturer Details", "Manufacturer's Name and Address", "Manufacturer Name"),
    "return_policy": ("Return Policy",),
}


def parse_product(html: str) -> Optional[Dict[str, Any]]:
    """Product page -> summary dict with price, stock and the attribute sheet."""
    state = extract_state(html)
    bff = ((((state.get("ui") or {}).get("pdp") or {}).get("bffPdp") or {}).get("bffData")) or {}
    seo = ((((bff.get("tracking") or {}).get("le_meta") or {}).get("custom_data")) or {}).get("seo") or {}
    product_id = _int(seo.get("id"))
    if not product_id:
        return None
    attributes: Dict[str, str] = {}
    for item in seo.get("attributes") or []:
        name, value = item.get("name"), item.get("value")
        if name and value not in (None, "") and name not in attributes:
            attributes[name] = str(value).strip()
    picked = {key: next((attributes[n] for n in names if n in attributes), None) for key, names in _ATTRIBUTE_KEYS.items()}
    merchant_id = None
    for snippet in bff.get("snippets") or []:
        if snippet.get("widget_type") == "product_atc_strip":
            blob = json.dumps(snippet.get("data") or {})
            found = re.search(r'"merchant_id":\s*(\d+)', blob)
            merchant_id = int(found.group(1)) if found else None
            break
    price, mrp = seo.get("price"), seo.get("mrp")
    inventory = seo.get("inventory") if isinstance(seo.get("inventory"), int) else 0
    available = bool(price) and inventory > 0
    cats = seo.get("categories") or {}
    return {
        "product_id": product_id,
        "name": seo.get("product_name"),
        "brand": seo.get("brand"),
        "pack_size": picked["unit"],
        "price_inr": price if price else None,
        "mrp_inr": mrp if mrp else None,
        "discount_pct": _pct(price, mrp) if price else 0,
        "in_stock": available,
        "inventory": max(inventory, 0),
        "shelf_life": picked["shelf_life"],
        "fssai_license": picked["fssai_license"],
        "country_of_origin": picked["country_of_origin"],
        "ingredients": picked["ingredients"],
        "manufacturer": picked["manufacturer"],
        "return_policy": picked["return_policy"],
        "category_ids": "/".join(str((cats.get(k) or {}).get("ID")) for k in ("L0Cat", "L1Cat", "L2Cat") if (cats.get(k) or {}).get("ID")),
        "merchant_id": merchant_id,
        "images": [u for u in seo.get("images") or [] if isinstance(u, str)],
        "attributes": attributes,
        "url": product_url(product_id, seo.get("product_name")),
    }
