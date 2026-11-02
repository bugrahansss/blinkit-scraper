# blinkit-scraper

[![PyPI](https://img.shields.io/pypi/v/blinkit-scraper)](https://pypi.org/project/blinkit-scraper/)
[![Python](https://img.shields.io/pypi/pyversions/blinkit-scraper)](https://pypi.org/project/blinkit-scraper/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Python scraper for [Blinkit](https://blinkit.com), India's 10-minute grocery app. Two jobs:

- **Category listings:** any category page (`/cn/chips-crisps/cid/1237/940`) → name, brand, pack size, price, MRP, discount, **units in stock**, rating, rating count, variants and URL, **for the dark store that serves your location**. Real pagination: 30 products on the first page, 15 per page after that, out-of-stock products kept.
- **Product pages:** price, MRP and stock plus the catalog sheet: **FSSAI licence, shelf life, country of origin, ingredients, manufacturer, return policy** and every other attribute Blinkit lists.

Output as Python dicts, JSON or CSV. One dependency (`requests`), no browser.

```bash
pip install blinkit-scraper
```

## Why a plain request fails

Blinkit sits behind Cloudflare. A script gets an error page, not products:

| Request | Result |
| --- | --- |
| `requests`, default or Chrome user agent | HTTP 403, "blinkit \| Error Page" |
| Through Scrape.do (`super=true&geoCode=in`) | 200 in ~3 s, full page with the data, 5 of 5 tries |

Pages are fetched through the [Scrape.do](https://scrape.do/?utm_source=github&utm_medium=readme&utm_campaign=blinkit-scraper) API with residential proxies in India. Blinkit requests cost 10 credits each; the free plan includes 1,000 credits a month.

## Quick start

```python
from blinkit_scraper import BlinkitScraper

scraper = BlinkitScraper(token="YOUR_SCRAPEDO_TOKEN", city="mumbai")  # or lat=..., lon=...

# 3 pages = 30 + 15 + 15 = 60 products, 3 requests
rows = scraper.get_category("https://blinkit.com/cn/chips-crisps/cid/1237/940", max_pages=3)
for row in rows[:3]:
    print(row["name"], row["pack_size"], row["price_inr"], row["mrp_inr"], row["inventory"], row["in_stock"])

product = scraper.get_product(rows[0]["url"])
print(product["fssai_license"], product["shelf_life"], product["country_of_origin"])
```

### Command line

```bash
export SCRAPEDO_TOKEN=your_token

# 3 pages of a category for a Mumbai address, CSV
blinkit-scraper category "https://blinkit.com/cn/chips-crisps/cid/1237/940" --pages 3 --city mumbai -f csv -o blinkit-chips.csv

# product sheets for the first 10 products of that file
blinkit-scraper product -i blinkit-chips.csv --limit 10 --city mumbai -f csv -o blinkit-products.csv

# any address: pass its coordinates
blinkit-scraper category "https://blinkit.com/cn/chips-crisps/cid/1237/940" --lat 12.9352 --lon 77.6245
```

City presets: `bengaluru`, `chennai`, `delhi`, `gurugram` (Blinkit's default), `hyderabad`, `kolkata`, `mumbai`, `pune`.

### Output

Listing row (from the fictional test page in [`tests/fixtures`](tests/fixtures/category_page.html)):

```json
{"product_id": 1001, "name": "Example Salted Chips", "brand": "Northwind Snacks", "pack_size": "60 g",
 "price_inr": 43, "mrp_inr": 70, "discount_pct": 38, "in_stock": true, "inventory": 5,
 "rating": 4.31, "rating_count": 120000, "variants": 2, "merchant_id": 90001,
 "image": "https://cdn.example.com/p/1001.png", "url": "https://blinkit.com/prn/example-salted-chips/prid/1001"}
```

Product columns: `product_id, name, brand, pack_size, price_inr, mrp_inr, discount_pct, in_stock, inventory, shelf_life, fssai_license, country_of_origin, category_ids, merchant_id, url` (plus `ingredients`, `manufacturer`, `return_policy`, `images` and the full `attributes` dict in Python/JSON).

## How it works

**The data is one JSON object.** Blinkit renders every page on the server and puts the data in `window.grofers.PRELOADED_STATE = {...}`. Category pages keep the product cards in `ui.plpContainer.feedData.snippets`, product pages keep the catalog sheet in `ui.pdp.bffPdp.bffData.tracking.le_meta.custom_data.seo`. The scraper reads the object with `json.JSONDecoder.raw_decode`. No CSS selectors.

**Each card carries the stock count.** `inventory` is the number of units the dark store has (Blinkit stops counting at 50). `rating_count` arrives as text in Indian units ("7 lac" = 700,000) and is converted.

**Location picks the store.** Prices, assortment and stock belong to one dark store, chosen from the delivery address. Without one, Blinkit uses Gurugram. In our test the chips category listed 673 products in Gurugram (store 31719) and 524 in Mumbai (store 41649); a banana chips pack had 5 units in one and 1 in the other. Pages read the location from the `gr_1_lat` / `gr_1_lon` cookies, the listing API from `lat` / `lon` headers; the scraper sends both.

**Pagination is a POST API.** The page holds 30 products and a `next_url` for `/v1/layout/listing_widgets`. That endpoint returns 15 products per call (a larger `limit` is ignored) and the next `next_url`. It only answers through residential proxies, so every request uses `super=true&geoCode=in`. Reading a whole category of 673 products takes 44 requests.

## Limits

- **Credits.** 10 Scrape.do credits per request: page 1 of a category, each further page of 15, each product page.
- **Stock cap.** `inventory` tops out at 50.
- **Search.** `/s/` search pages are disallowed in Blinkit's robots.txt and not supported; use category pages.
- **Errors.** Non-200 responses raise `ScrapeError` with the status code; `from_target` tells a Blinkit status from a Scrape.do one (a wrong token is a Scrape.do 401). A 200 without Blinkit's data is retried. Server errors, 429s and empty pages are retried twice; client errors are not.

## Responsible use

The scraper reads category (`/cn/`) and product (`/prn/`) pages, which Blinkit's robots.txt allows, plus the listing API those pages call. It never touches search, cart, checkout or account paths. Product data is not personal data. Read Blinkit's terms for your use case and keep request rates modest. This is not legal advice.

## Development

```bash
git clone https://github.com/bugrahansss/blinkit-scraper
cd blinkit-scraper
pip install -e .
python -m unittest discover -s tests -v
```

The tests run offline against fictional fixture pages.

## License

MIT

---

Built and tested with [Scrape.do](https://scrape.do/?utm_source=github&utm_medium=readme&utm_campaign=blinkit-scraper), November 2026.
