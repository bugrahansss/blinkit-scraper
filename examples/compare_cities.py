"""Same category, two cities: which products and how much stock each dark store has."""
from blinkit_scraper import BlinkitScraper

URL = "https://blinkit.com/cn/chips-crisps/cid/1237/940"

stores = {}
for city in ("gurugram", "mumbai"):
    scraper = BlinkitScraper(city=city)  # token from SCRAPEDO_TOKEN
    info = scraper.get_category(URL, max_pages=2, with_info=True)
    stores[city] = {row["product_id"]: row for row in info["products"]}
    print(f"{city}: store {info['merchant_id']}, {info['total']} products listed, {scraper.credits_used} credits")

both = stores["gurugram"].keys() & stores["mumbai"].keys()
print(f"{len(both)} products on page 1-2 in both cities")
for pid in list(both)[:10]:
    g, m = stores["gurugram"][pid], stores["mumbai"][pid]
    print(f"{g['name'][:40]:40} {g['price_inr']:>5} {m['price_inr']:>5}   stock {g['inventory']:>3} / {m['inventory']:>3}")
