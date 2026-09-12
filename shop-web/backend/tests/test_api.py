import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = os.getenv("TEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DB_PATH = os.getenv("TEST_DB_PATH", os.getenv("DB_PATH", "/data/shop.db"))
NO_BODY = object()
PRODUCT_FIELDS = ("name", "category", "price", "icon", "color", "description")


class ApiTests(unittest.TestCase):
    """Integration tests for the shop API.

    The tests create uniquely named products instead of depending on the seed
    catalog, so they can be repeated against a persistent development volume.
    Products and carts are removed in tearDown; orders intentionally remain as
    the API has no order deletion operation and their snapshots are immutable.
    """

    def setUp(self):
        self.created_products = []
        self.created_carts = []

    def tearDown(self):
        for cart_id in reversed(self.created_carts):
            status, _ = self.request(f"/api/carts/{cart_id}", "DELETE")
            self.assertIn(status, (204, 404), f"清理購物車失敗: {cart_id} -> {status}")
        for product_id in reversed(self.created_products):
            status, _ = self.request(f"/api/products/{product_id}", "DELETE")
            self.assertIn(status, (204, 404), f"清理商品失敗: {product_id} -> {status}")

    def request(self, path, method="GET", payload=NO_BODY):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not NO_BODY:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{BASE}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, self.decode(response.read())
        except HTTPError as error:
            return error.code, self.decode(error.read())

    @staticmethod
    def decode(raw):
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def product_payload(**overrides):
        token = uuid.uuid4().hex[:12]
        payload = {
            "name": f"測試商品 {token}",
            "category": "測試分類",
            "price": 271,
            "icon": "🧪",
            "color": "#D6E4D8",
            "description": f"整合測試商品 {token}",
        }
        payload.update(overrides)
        return payload

    def assert_product(self, product, expected=None):
        self.assertIsInstance(product, dict)
        self.assertIsInstance(product.get("id"), int)
        for field in PRODUCT_FIELDS:
            self.assertIn(field, product)
        if expected:
            for field, value in expected.items():
                self.assertEqual(product[field], value, field)

    def create_product(self, **overrides):
        payload = self.product_payload(**overrides)
        status, product = self.request("/api/products", "POST", payload)
        self.assertEqual(status, 201, product)
        self.assert_product(product)
        self.created_products.append(product["id"])
        return product

    def create_cart(self):
        status, cart = self.request("/api/carts", "POST")
        self.assertEqual(status, 201, cart)
        self.assert_cart(cart, expected_items=[])
        self.created_carts.append(cart["id"])
        return cart

    def assert_cart(self, cart, expected_items=None):
        self.assertIsInstance(cart, dict)
        for field in ("id", "items", "total"):
            self.assertIn(field, cart)
        self.assertIsInstance(cart["items"], list)
        self.assertIsInstance(cart["total"], int)
        if expected_items is not None:
            self.assertEqual(cart["items"], expected_items)

    def assert_cart_item(self, item, product, quantity):
        self.assertIsInstance(item, dict)
        for field in (*PRODUCT_FIELDS, "product_id", "quantity", "line_total"):
            self.assertIn(field, item)
        self.assertEqual(item["id"], product["id"])
        self.assertEqual(item["product_id"], product["id"])
        self.assertEqual(item["name"], product["name"])
        self.assertEqual(item["category"], product["category"])
        self.assertEqual(item["price"], product["price"])
        self.assertEqual(item["icon"], product["icon"])
        self.assertEqual(item["color"], product["color"])
        self.assertEqual(item["description"], product["description"])
        self.assertEqual(item["quantity"], quantity)
        self.assertEqual(item["line_total"], product["price"] * quantity)

    @staticmethod
    def item_for(cart, product_id):
        return next(item for item in cart["items"] if item["product_id"] == product_id)

    def checkout_order(self, cart_id, name="測試顧客", address="台北市測試路 1 號"):
        return self.request(
            f"/api/carts/{cart_id}/checkout",
            "POST",
            {"name": name, "address": address},
        )

    def read_saved_order(self, order_id):
        with sqlite3.connect(DB_PATH) as db:
            row = db.execute("SELECT created_at, payload FROM orders WHERE id = ?", (order_id,)).fetchone()
        self.assertIsNotNone(row, f"找不到已保存訂單 {order_id}")
        created_at, payload = row
        return created_at, json.loads(payload)

    def test_products_crud_and_trimmed_fields(self):
        self.assertEqual(self.request("/api/health"), (200, {"status": "ok"}))
        status, products = self.request("/api/products")
        self.assertEqual(status, 200)
        self.assertIsInstance(products, list)

        product = self.create_product(
            name="  可更新商品  ",
            category="  居家測試  ",
            icon="  🧪  ",
            color="#D6E4D8",
            description="  建立時會去除兩側空白  ",
        )
        product_id = product["id"]
        self.assert_product(
            product,
            {
                "name": "可更新商品",
                "category": "居家測試",
                "icon": "🧪",
                "description": "建立時會去除兩側空白",
            },
        )

        status, fetched = self.request(f"/api/products/{product_id}")
        self.assertEqual(status, 200)
        self.assert_product(fetched, {"id": product_id, "price": product["price"]})

        replacement = {
            "name": "  更新後商品  ",
            "category": "  文具測試  ",
            "price": 845,
            "icon": "  📒  ",
            "color": "#F2D2C9",
            "description": "  完整 PUT 內容  ",
        }
        status, replaced = self.request(f"/api/products/{product_id}", "PUT", replacement)
        self.assertEqual(status, 200, replaced)
        self.assert_product(
            replaced,
            {"id": product_id, "name": "更新後商品", "category": "文具測試", "price": 845, "icon": "📒", "description": "完整 PUT 內容"},
        )

        status, patched = self.request(f"/api/products/{product_id}", "PATCH", {"name": "  局部更新  "})
        self.assertEqual(status, 200, patched)
        self.assert_product(patched, {"id": product_id, "name": "局部更新", "price": 845, "color": "#F2D2C9"})

        status, body = self.request(f"/api/products/{product_id}", "DELETE")
        self.assertEqual(status, 204, body)
        self.assertIsNone(body)
        self.created_products.remove(product_id)
        self.assertEqual(self.request(f"/api/products/{product_id}")[0], 404)

    def test_product_payload_validation_and_strict_price(self):
        valid = self.product_payload()
        invalid_payloads = [
            {"name": valid["name"]},
            {**valid, "name": None},
            {**valid, "price": True},
            {**valid, "price": 1.5},
            {**valid, "price": "271"},
            {**valid, "price": 0},
            {**valid, "price": -1},
            {**valid, "price": 1_000_000_001},
            {**valid, "description": None},
            {**valid, "description": "x" * 2001},
            {**valid, "color": "#12345"},
            {**valid, "color": "red"},
            {**valid, "unknown": "拒絕"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                status, _ = self.request("/api/products", "POST", payload)
                self.assertEqual(status, 422)

        empty_description = self.create_product(description="")
        self.assertEqual(empty_description["description"], "")
        product_id = empty_description["id"]
        full = self.product_payload(price=1_000_000_000)
        status, updated = self.request(f"/api/products/{product_id}", "PUT", full)
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["price"], 1_000_000_000)

        for method, payload in (
            ("PUT", {"name": "缺少欄位"}),
            ("PUT", {**full, "unknown": "拒絕"}),
            ("PATCH", {}),
            ("PATCH", {"name": None}),
            ("PATCH", {"unknown": "拒絕"}),
            ("PATCH", {"price": True}),
            ("PATCH", {"price": 1_000_000_001}),
            ("PATCH", {"color": "#GGGGGG"}),
        ):
            with self.subTest(method=method, payload=payload):
                status, _ = self.request(f"/api/products/{product_id}", method, payload)
                self.assertEqual(status, 422)

    def test_cart_lifecycle_and_item_mutations(self):
        product = self.create_product(price=120)
        product_id = product["id"]
        cart = self.create_cart()
        cart_id = cart["id"]

        status, fetched = self.request(f"/api/carts/{cart_id}")
        self.assertEqual(status, 200)
        self.assert_cart(fetched, expected_items=[])

        status, cart = self.request(f"/api/carts/{cart_id}/items", "POST", {"product_id": product_id, "quantity": 2})
        self.assertEqual(status, 200, cart)
        self.assert_cart(cart)
        self.assertEqual(cart["total"], 240)
        self.assert_cart_item(self.item_for(cart, product_id), product, 2)

        status, items = self.request(f"/api/carts/{cart_id}/items")
        self.assertEqual(status, 200)
        self.assertEqual(len(items), 1)
        self.assert_cart_item(items[0], product, 2)

        status, cart = self.request(f"/api/carts/{cart_id}/items/{product_id}", "PATCH", {"quantity": 5})
        self.assertEqual(status, 200, cart)
        self.assertEqual(cart["total"], 600)
        self.assert_cart_item(self.item_for(cart, product_id), product, 5)

        status, cart = self.request(f"/api/carts/{cart_id}/items/{product_id}", "DELETE")
        self.assertEqual(status, 200, cart)
        self.assert_cart(cart, expected_items=[])

        status, cart = self.request(f"/api/carts/{cart_id}/items", "POST", {"product_id": product_id, "quantity": 1})
        self.assertEqual(status, 200, cart)
        status, cleared = self.request(f"/api/carts/{cart_id}/items", "DELETE")
        self.assertEqual(status, 200, cleared)
        self.assert_cart(cleared, expected_items=[])

        status, body = self.request(f"/api/carts/{cart_id}", "DELETE")
        self.assertEqual(status, 204, body)
        self.created_carts.remove(cart_id)
        self.assertEqual(self.request(f"/api/carts/{cart_id}")[0], 404)

    def test_cart_validation_boundaries_and_two_cart_isolation(self):
        product = self.create_product(price=99)
        product_id = product["id"]
        first = self.create_cart()
        second = self.create_cart()

        missing_product = self.create_product(price=101)
        missing_product_id = missing_product["id"]
        status, body = self.request(f"/api/products/{missing_product_id}", "DELETE")
        self.assertEqual(status, 204, body)
        self.created_products.remove(missing_product_id)

        status, cart = self.request(f"/api/carts/{first['id']}/items", "POST", {"product_id": product_id, "quantity": 99})
        self.assertEqual(status, 200, cart)
        self.assertEqual(self.item_for(cart, product_id)["quantity"], 99)

        status, _ = self.request(f"/api/carts/{first['id']}/items", "POST", {"product_id": product_id, "quantity": 1})
        self.assertEqual(status, 400)
        status, cart = self.request(f"/api/carts/{first['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(self.item_for(cart, product_id)["quantity"], 99)

        invalid_quantities = (0, 100, 1.5, True)
        for quantity in invalid_quantities:
            with self.subTest(quantity=quantity):
                status, _ = self.request(f"/api/carts/{second['id']}/items", "POST", {"product_id": product_id, "quantity": quantity})
                self.assertEqual(status, 422)
                status, _ = self.request(f"/api/carts/{first['id']}/items/{product_id}", "PATCH", {"quantity": quantity})
                self.assertEqual(status, 422)

        status, cart = self.request(f"/api/carts/{second['id']}/items", "POST", {"product_id": product_id, "quantity": 1})
        self.assertEqual(status, 200, cart)
        self.assertEqual(cart["total"], 99)
        status, first_after = self.request(f"/api/carts/{first['id']}")
        self.assertEqual(status, 200)
        status, second_after = self.request(f"/api/carts/{second['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(self.item_for(first_after, product_id)["quantity"], 99)
        self.assertEqual(self.item_for(second_after, product_id)["quantity"], 1)

        missing_cart = uuid.uuid4().hex
        for path, method, payload in (
            (f"/api/carts/{missing_cart}", "GET", NO_BODY),
            (f"/api/carts/{missing_cart}/items", "GET", NO_BODY),
            (f"/api/carts/{missing_cart}/items", "POST", {"product_id": product_id, "quantity": 1}),
            (f"/api/carts/{missing_cart}/items", "DELETE", NO_BODY),
            (f"/api/carts/{missing_cart}", "DELETE", NO_BODY),
            (f"/api/carts/{second['id']}/items/{missing_product_id}", "PATCH", {"quantity": 1}),
            (f"/api/carts/{second['id']}/items/{missing_product_id}", "DELETE", NO_BODY),
        ):
            with self.subTest(path=path, method=method):
                status, _ = self.request(path, method, payload)
                self.assertEqual(status, 404)
        self.assertEqual(self.request(f"/api/products/{missing_product_id}")[0], 404)
        for invalid_product_id in (0, -1):
            with self.subTest(invalid_product_id=invalid_product_id):
                status, _ = self.request(
                    f"/api/carts/{second['id']}/items",
                    "POST",
                    {"product_id": invalid_product_id, "quantity": 1},
                )
                self.assertEqual(status, 422)

    def test_concurrent_cart_additions_do_not_lose_updates(self):
        product = self.create_product(price=37)
        cart = self.create_cart()
        workers = 8

        def add_one(_):
            return self.request(
                f"/api/carts/{cart['id']}/items",
                "POST",
                {"product_id": product["id"], "quantity": 1},
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            responses = list(executor.map(add_one, range(workers)))
        self.assertTrue(all(status == 200 for status, _ in responses), responses)

        status, final_cart = self.request(f"/api/carts/{cart['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(final_cart["total"], product["price"] * workers)
        self.assertEqual(self.item_for(final_cart, product["id"])["quantity"], workers)

    def test_concurrent_checkout_is_atomic_and_creates_one_order(self):
        product = self.create_product(price=43)
        cart = self.create_cart()
        status, body = self.request(
            f"/api/carts/{cart['id']}/items",
            "POST",
            {"product_id": product["id"], "quantity": 2},
        )
        self.assertEqual(status, 200, body)
        with sqlite3.connect(DB_PATH) as db:
            before_count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

        def checkout_once(_):
            return self.checkout_order(cart["id"], name="並發顧客", address="台北市並發路 2 號")

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(checkout_once, range(2)))
        self.assertEqual(sorted(status for status, _ in responses), [201, 400], responses)
        successful = [body for status, body in responses if status == 201]
        self.assertEqual(len(successful), 1)
        self.assertEqual(successful[0]["total"], product["price"] * 2)
        with sqlite3.connect(DB_PATH) as db:
            after_count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        self.assertEqual(after_count, before_count + 1)
        status, emptied = self.request(f"/api/carts/{cart['id']}")
        self.assertEqual(status, 200)
        self.assert_cart(emptied, expected_items=[])

    def test_price_change_recalculates_cart_and_checkout_uses_current_price(self):
        product = self.create_product(price=120)
        cart = self.create_cart()
        status, cart_body = self.request(
            f"/api/carts/{cart['id']}/items",
            "POST",
            {"product_id": product["id"], "quantity": 2},
        )
        self.assertEqual(status, 200, cart_body)

        updated_payload = self.product_payload(
            name=product["name"],
            category=product["category"],
            price=250,
            icon=product["icon"],
            color=product["color"],
            description=product["description"],
        )
        status, updated = self.request(f"/api/products/{product['id']}", "PUT", updated_payload)
        self.assertEqual(status, 200, updated)
        product.update(updated)

        status, current_cart = self.request(f"/api/carts/{cart['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(current_cart["total"], 500)
        self.assert_cart_item(self.item_for(current_cart, product["id"]), product, 2)

        status, order = self.checkout_order(cart["id"], name="  現價顧客  ", address="  台北市現價路 8 號  ")
        self.assertEqual(status, 201, order)
        self.assertEqual(order["total"], 500)
        self.assertEqual(order["items"][0]["price"], 250)
        self.assertEqual(order["items"][0]["quantity"], 2)

        status, emptied = self.request(f"/api/carts/{cart['id']}")
        self.assertEqual(status, 200)
        self.assert_cart(emptied, expected_items=[])
        status, _ = self.checkout_order(cart["id"])
        self.assertEqual(status, 400)

    def test_delete_product_cascades_cart_items_and_preserves_order_snapshot(self):
        product = self.create_product(price=333)
        order_cart = self.create_cart()
        status, _ = self.request(
            f"/api/carts/{order_cart['id']}/items",
            "POST",
            {"product_id": product["id"], "quantity": 2},
        )
        self.assertEqual(status, 200)
        status, order = self.checkout_order(order_cart["id"])
        self.assertEqual(status, 201, order)
        created_at_before, payload_before = self.read_saved_order(order["id"])
        self.assertEqual(created_at_before, order["created_at"])
        self.assertEqual(payload_before["items"], order["items"])

        cart_with_item = self.create_cart()
        status, _ = self.request(
            f"/api/carts/{cart_with_item['id']}/items",
            "POST",
            {"product_id": product["id"], "quantity": 3},
        )
        self.assertEqual(status, 200)

        status, body = self.request(f"/api/products/{product['id']}", "DELETE")
        self.assertEqual(status, 204, body)
        self.created_products.remove(product["id"])
        self.assertEqual(self.request(f"/api/products/{product['id']}")[0], 404)
        status, deleted_cart = self.request(f"/api/carts/{cart_with_item['id']}")
        self.assertEqual(status, 200)
        self.assert_cart(deleted_cart, expected_items=[])

        created_at_after, payload_after = self.read_saved_order(order["id"])
        self.assertEqual(created_at_after, created_at_before)
        self.assertEqual(payload_after, payload_before)

    def test_legacy_orders_use_server_price_validate_and_persist(self):
        product = self.create_product(price=217)
        payload = {
            "name": "  持久化顧客  ",
            "address": "  台北市持久化路 1 號  ",
            "items": [
                {"product_id": product["id"], "quantity": 2},
                {"product_id": product["id"], "quantity": 1},
            ],
        }
        status, order = self.request("/api/orders", "POST", payload)
        self.assertEqual(status, 201, order)
        self.assertEqual(order["total"], product["price"] * 3)
        self.assertEqual(len(order["items"]), 1)
        self.assertEqual(order["items"][0]["quantity"], 3)
        created_at, saved = self.read_saved_order(order["id"])
        self.assertEqual(created_at, order["created_at"])
        self.assertEqual(saved["name"], "持久化顧客")
        self.assertEqual(saved["address"], "台北市持久化路 1 號")
        self.assertEqual(saved["items"], order["items"])

        missing_product = self.create_product(price=219)
        missing_product_id = missing_product["id"]
        status, body = self.request(f"/api/products/{missing_product_id}", "DELETE")
        self.assertEqual(status, 204, body)
        self.created_products.remove(missing_product_id)
        status, _ = self.request("/api/orders", "POST", {**payload, "items": [{"product_id": missing_product_id, "quantity": 1}]})
        self.assertEqual(status, 400)
        for item in (
            {"product_id": 0, "quantity": 1},
            {"product_id": -1, "quantity": 1},
            {"product_id": product["id"], "quantity": 0},
            {"product_id": product["id"], "quantity": 100},
            {"product_id": product["id"], "quantity": 1.5},
            {"product_id": product["id"], "quantity": True},
            {"product_id": product["id"], "quantity": 1, "price": 1},
        ):
            with self.subTest(item=item):
                status, _ = self.request("/api/orders", "POST", {**payload, "items": [item]})
                self.assertEqual(status, 422)
        for invalid_payload in (
            {**payload, "items": []},
            {**payload, "name": "   "},
            {**payload, "address": "     "},
        ):
            with self.subTest(invalid_payload=invalid_payload):
                status, _ = self.request("/api/orders", "POST", invalid_payload)
                self.assertEqual(status, 422)

    def test_legacy_database_migration_is_idempotent_and_preserves_order(self):
        backend_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="shop-api-migration-") as directory:
            db_path = Path(directory) / "legacy.db"
            legacy_payload = json.dumps({"id": "legacy-order", "total": 42}, ensure_ascii=False)
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
                db.execute("INSERT INTO orders VALUES (?, ?, ?)", ("legacy-order", "2026-01-01T00:00:00+00:00", legacy_payload))

            environment = os.environ.copy()
            environment["DB_PATH"] = str(db_path)
            environment["PYTHONPATH"] = str(backend_dir)
            command = [sys.executable, "-c", "from app.main import init_db; init_db(); init_db()"]
            result = subprocess.run(command, cwd=backend_dir, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

            with sqlite3.connect(db_path) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                self.assertIn("products", tables)
                before = db.execute("SELECT id FROM products ORDER BY id").fetchall()
                old_order = db.execute("SELECT payload FROM orders WHERE id = 'legacy-order'").fetchone()
            self.assertGreater(len(before), 0)
            self.assertEqual(len(before), len({row[0] for row in before}))
            self.assertIsNotNone(old_order)

            result = subprocess.run(command[:], cwd=backend_dir, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            with sqlite3.connect(db_path) as db:
                after_second_init = db.execute("SELECT id FROM products ORDER BY id").fetchall()
            self.assertEqual(after_second_init, before)

            removed_id = before[0][0]
            with sqlite3.connect(db_path) as db:
                db.execute("DELETE FROM products WHERE id = ?", (removed_id,))
            result = subprocess.run(command[:], cwd=backend_dir, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            with sqlite3.connect(db_path) as db:
                after_delete_and_init = db.execute("SELECT id FROM products ORDER BY id").fetchall()
                preserved_order = db.execute("SELECT payload FROM orders WHERE id = 'legacy-order'").fetchone()
            self.assertEqual(after_delete_and_init, [row for row in before if row[0] != removed_id])
            self.assertEqual(preserved_order, old_order)



if __name__ == "__main__":
    unittest.main()
