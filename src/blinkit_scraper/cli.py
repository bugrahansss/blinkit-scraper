"""Command line entry point: ``blinkit-scraper category URL`` / ``blinkit-scraper product URL ...``."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import List, Optional

from . import __version__
from .client import CITIES, TOKEN_ENV, BlinkitScraper, ScrapeError
from .parser import LISTING_FIELDS, PRODUCT_FIELDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blinkit-scraper",
        description="Scrape Blinkit categories and product pages (price, MRP, stock per dark store) into JSON or CSV.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    cat = sub.add_parser("category", help="Products of a category page (30 on page 1, 15 per further page)")
    cat.add_argument("url", help="A Blinkit category URL, e.g. https://blinkit.com/cn/chips-crisps/cid/1237/940")
    cat.add_argument("--pages", type=int, default=1, help="Pages to read (default 1)")

    prod = sub.add_parser("product", help="Product pages: price, stock, FSSAI licence, shelf life, attributes")
    prod.add_argument("urls", nargs="*", help="Blinkit product URLs or product ids")
    prod.add_argument("-i", "--input", help="Text file with one URL per line, or a CSV with a 'url' column")
    prod.add_argument("--limit", type=int, default=0, help="Stop after this many products (0 = all)")

    for p in (cat, prod):
        p.add_argument("-t", "--token", help=f"Scrape.do API token (default: ${TOKEN_ENV})")
        p.add_argument("--city", choices=sorted(CITIES), help="Delivery location preset (default: Gurugram)")
        p.add_argument("--lat", type=float, help="Delivery latitude (with --lon)")
        p.add_argument("--lon", type=float, help="Delivery longitude (with --lat)")
        p.add_argument("-f", "--format", choices=("json", "csv"), default="json")
        p.add_argument("-o", "--output", help="Write to this file instead of stdout")
    return parser


def _read_urls(args: argparse.Namespace) -> List[str]:
    urls = list(args.urls)
    if args.input:
        with open(args.input, encoding="utf-8", newline="") as handle:
            if args.input.endswith(".csv"):
                urls.extend(row["url"] for row in csv.DictReader(handle) if row.get("url"))
            else:
                urls.extend(line.strip() for line in handle if line.strip() and not line.startswith("#"))
    return urls[: args.limit] if args.limit else urls


def _write(rows: List[dict], fields: List[str], fmt: str, stream) -> None:
    if fmt == "csv":
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    else:
        json.dump(rows, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if (args.lat is None) != (args.lon is None):
        print("Pass both --lat and --lon.", file=sys.stderr)
        return 2
    try:
        scraper = BlinkitScraper(token=args.token, lat=args.lat, lon=args.lon, city=args.city)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    failures = 0
    if args.command == "category":
        fields = LISTING_FIELDS
        try:
            info = scraper.get_category(args.url, max_pages=max(1, args.pages), with_info=True)
        except (ScrapeError, ValueError) as exc:
            print(f"fail  {args.url}: {exc}", file=sys.stderr)
            return 1
        rows = info["products"]
        print(f"{len(rows)} of {info['total']} products, store {info['merchant_id']} ({info['locality']}), "
              f"{scraper.credits_used} credits", file=sys.stderr)
    else:
        fields = PRODUCT_FIELDS
        urls = _read_urls(args)
        if not urls:
            print("No URLs given. Pass product URLs or --input FILE.", file=sys.stderr)
            return 2
        rows = []
        for url in urls:
            try:
                product = scraper.get_product(url)
            except (ScrapeError, ValueError) as exc:
                failures += 1
                print(f"fail  {url}: {exc}", file=sys.stderr)
                continue
            if not product:
                failures += 1
                print(f"fail  {url}: no product data", file=sys.stderr)
                continue
            rows.append(product)
            print(f"ok    {url}", file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="" if args.format == "csv" else None) as handle:
            _write(rows, fields, args.format, handle)
        print(f"Saved {len(rows)} rows to {args.output}", file=sys.stderr)
    else:
        _write(rows, fields, args.format, sys.stdout)
    return 1 if failures and not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
