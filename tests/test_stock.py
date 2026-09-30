from __future__ import annotations

import unittest

from amul_watch.stock import parse_product_stock


class StockParserTest(unittest.TestCase):
    def test_pack_of_30_preferred(self) -> None:
        product = {
            "name": "Amul High Protein Rose Lassi, 200 mL | Pack of 30",
            "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
            "variants": [
                {"name": "Single 200 mL", "sku": "LASCP40_1", "inventory_quantity": 5, "available": True, "price": 35},
                {"name": "Pack of 30", "sku": "LASCP40_30", "inventory_quantity": 2, "available": True, "price": 900},
            ],
        }
        stock = parse_product_stock(product, alias=product["alias"], prefer_pack_of_30=True)
        self.assertTrue(stock.any_in_stock)
        assert stock.best is not None
        self.assertTrue(stock.best.is_pack_of_30)
        self.assertEqual(stock.best.qty, 2)

    def test_out_of_stock(self) -> None:
        product = {
            "name": "Buttermilk",
            "variants": [{"name": "Pack of 30", "sku": "BM_30", "inventory_quantity": 0, "available": False}],
        }
        stock = parse_product_stock(product, alias="amul-high-protein-buttermilk-200-ml-or-pack-of-30")
        self.assertFalse(stock.any_in_stock)


if __name__ == "__main__":
    unittest.main()
