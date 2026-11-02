import json
import os
import unittest
from unittest import mock

from blinkit_scraper import BlinkitScraper, ScrapeError, parse_category, parse_count, parse_listing_api, parse_product
from blinkit_scraper.parser import category_ids, extract_state, product_id_from_url, product_url

HERE = os.path.dirname(__file__)


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as handle:
        return handle.read()


CATEGORY = fixture("category_page.html")
PRODUCT = fixture("product_page.html")
API = json.loads(fixture("listing_api.json"))
URL = "https://blinkit.com/cn/example-snacks/cid/900/901"


class Response:
    def __init__(self, status, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


class ParserTests(unittest.TestCase):
    def test_extract_state(self):
        self.assertIn("ui", extract_state(CATEGORY))
        self.assertEqual(extract_state("<html>nothing</html>"), {})

    def test_category_cards(self):
        page = parse_category(CATEGORY)
        self.assertEqual([r["product_id"] for r in page["products"]], [1001, 1002, 1003])
        self.assertEqual(page["total"], 5)
        self.assertEqual(page["locality"], "Example Town")
        self.assertTrue(page["next_url"].startswith("/v1/layout/listing_widgets"))

    def test_card_fields(self):
        row = parse_category(CATEGORY)["products"][0]
        self.assertEqual(row["price_inr"], 43)
        self.assertEqual(row["mrp_inr"], 70)
        self.assertEqual(row["discount_pct"], 38)  # rounded down, as Blinkit shows it
        self.assertEqual(row["inventory"], 5)
        self.assertTrue(row["in_stock"])
        self.assertEqual(row["rating"], 4.31)
        self.assertEqual(row["rating_count"], 120000)
        self.assertEqual(row["variants"], 2)
        self.assertEqual(row["pack_size"], "60 g")
        self.assertEqual(row["url"], "https://blinkit.com/prn/example-salted-chips/prid/1001")

    def test_out_of_stock_kept(self):
        row = parse_category(CATEGORY)["products"][2]
        self.assertFalse(row["in_stock"])
        self.assertEqual(row["inventory"], 0)
        self.assertIsNone(row["rating"])

    def test_listing_api(self):
        page = parse_listing_api(API)
        self.assertEqual([r["product_id"] for r in page["products"]], [1004, 1002])
        self.assertIsNone(page["next_url"])
        self.assertEqual(page["products"][0]["rating_count"], 15000)

    def test_product(self):
        p = parse_product(PRODUCT)
        self.assertEqual(p["product_id"], 1001)
        self.assertEqual(p["shelf_life"], "180 days")
        self.assertEqual(p["fssai_license"], "10000000000001")
        self.assertEqual(p["country_of_origin"], "India")
        self.assertEqual(p["pack_size"], "60 g")
        self.assertEqual(p["manufacturer"], "Example Foods Pvt Ltd, Example Road, Example City")
        self.assertEqual(p["category_ids"], "900/901/902")
        self.assertEqual(p["merchant_id"], 90001)
        self.assertEqual(len(p["images"]), 2)
        self.assertTrue(p["in_stock"])

    def test_product_missing(self):
        self.assertIsNone(parse_product(CATEGORY))

    def test_counts(self):
        self.assertEqual(parse_count("7 lac"), 700000)
        self.assertEqual(parse_count("1.2k"), 1200)
        self.assertEqual(parse_count("(980)"), 980)
        self.assertIsNone(parse_count(None))

    def test_urls(self):
        self.assertEqual(category_ids(URL), (900, 901))
        self.assertIsNone(category_ids("https://blinkit.com/s/?q=chips"))
        self.assertEqual(product_id_from_url("https://blinkit.com/prn/x/prid/1001"), 1001)
        self.assertEqual(product_url(5, "Lay's Magic Masala"), "https://blinkit.com/prn/lay-s-magic-masala/prid/5")


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def fake(self, routes):
        def request(method, url, params=None, headers=None, data=None, timeout=None):
            self.calls.append((method, params, headers))
            return routes(method, params)
        session = mock.Mock()
        session.request.side_effect = request
        return session

    def test_category_pages_and_location(self):
        def routes(method, params):
            if method == "POST":
                return Response(200, json.dumps(API), {"scrape.do-request-cost": "10"})
            return Response(200, CATEGORY, {"scrape.do-request-cost": "10"})
        scraper = BlinkitScraper(token="T", city="mumbai", session=self.fake(routes))
        info = scraper.get_category(URL, max_pages=3, with_info=True)
        self.assertEqual(len(info["products"]), 4)  # 3 from the page, 1 new from the API (1002 deduped)
        self.assertEqual(info["pages_read"], 2)       # stops when the API has no next_url
        self.assertFalse(info["has_more"])
        page_call, api_call = self.calls
        self.assertIn("gr_1_lat=19.076", page_call[1]["setCookies"])
        self.assertEqual(page_call[1]["super"], "true")
        self.assertEqual(api_call[1]["extraHeaders"], "true")
        self.assertEqual(api_call[2]["sd-lat"], "19.076")
        self.assertTrue(api_call[1]["url"].startswith("https://blinkit.com/v1/layout/listing_widgets"))
        self.assertEqual(scraper.credits_used, 20)

    def test_one_page_no_api(self):
        scraper = BlinkitScraper(token="T", session=self.fake(lambda m, p: Response(200, CATEGORY)))
        self.assertEqual(len(scraper.get_category(URL)), 3)
        self.assertEqual(len(self.calls), 1)

    def test_product(self):
        scraper = BlinkitScraper(token="T", session=self.fake(lambda m, p: Response(200, PRODUCT)))
        self.assertEqual(scraper.get_product("https://blinkit.com/prn/x/prid/1001")["fssai_license"], "10000000000001")
        self.assertTrue(self.calls[0][1]["url"].endswith("/prid/1001"))

    def test_bad_token(self):
        scraper = BlinkitScraper(token="T", session=self.fake(lambda m, p: Response(401, "")))
        with self.assertRaises(ScrapeError) as ctx:
            scraper.get_product(1001)
        self.assertFalse(ctx.exception.from_target)
        self.assertEqual(len(self.calls), 1)

    def test_page_without_data_retried(self):
        scraper = BlinkitScraper(token="T", retries=2, session=self.fake(lambda m, p: Response(200, "<html>challenge</html>")))
        with mock.patch("blinkit_scraper.client.time.sleep"), self.assertRaises(ScrapeError):
            scraper.get_product(1001)
        self.assertEqual(len(self.calls), 3)

    def test_blocked_403_not_retried(self):
        scraper = BlinkitScraper(token="T", session=self.fake(lambda m, p: Response(403, "x", {"scrape.do-initial-status-code": "403"})))
        with self.assertRaises(ScrapeError) as ctx:
            scraper.get_product(1001)
        self.assertTrue(ctx.exception.from_target)
        self.assertEqual(len(self.calls), 1)

    def test_validation(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            BlinkitScraper()
        with self.assertRaises(ValueError):
            BlinkitScraper(token="T", city="atlantis")
        with self.assertRaises(ValueError):
            BlinkitScraper(token="T").get_category("https://blinkit.com/s/?q=chips")


if __name__ == "__main__":
    unittest.main()
