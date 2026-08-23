import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, _tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])


class V048UiUxContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.worker = (ROOT / "service-worker.js").read_text(encoding="utf-8")
        cls.manifest = (ROOT / "manifest.webmanifest").read_text(encoding="utf-8")

    def test_approved_home_design_is_integrated(self):
        for marker in (
            "Dashboard v0.49",
            'class="home-overview"',
            'id="heroAttention"',
            'id="heroTrackedSummary"',
            'id="heroSavingsSummary"',
            'class="dashboard-board"',
            'class="app-bottom-nav"',
            'data-app-nav="inventory"',
            'data-app-nav="shopping"',
            'data-app-nav="savings"',
        ):
            self.assertIn(marker, self.source)

    def test_real_v047_features_are_preserved(self):
        for marker in (
            'window.supabase.createClient',
            'id="createShoppingBasket"',
            'id="manageInventory"',
            'id="barcodeStart"',
            'id="monthlySavings"',
            'id="family"',
            'function buildShoppingBaskets(wanted,nearby,priceRows)',
            'sb.rpc("change_product_inventory"',
            'product.need_key==="formula"&&!preferred',
            'sb.from("household_monthly_savings_v047")',
        ):
            self.assertIn(marker, self.source)

    def test_accessible_mobile_foundations_exist(self):
        for marker in (
            '<html lang="he" dir="rtl">',
            'aria-label="פעולות מהירות"',
            'aria-label="ניווט ראשי"',
            '@media(max-width:620px)',
            '@media(max-width:380px)',
            '@media(prefers-reduced-motion:reduce)',
            'button:focus-visible',
            'min-width:320px',
        ):
            self.assertIn(marker, self.source)

    def test_static_ids_are_unique(self):
        parser = IdCollector()
        parser.feed(self.source)
        duplicates = [name for name, count in Counter(parser.ids).items() if count > 1]
        self.assertEqual(duplicates, [])

    def test_versioned_cache_and_manifest_match_the_design(self):
        self.assertIn('baby-smart-v049-special-retailer-import', self.worker)
        self.assertIn('"background_color": "#f6f5fa"', self.manifest)
        self.assertIn('"theme_color": "#6555e7"', self.manifest)


if __name__ == "__main__":
    unittest.main()
