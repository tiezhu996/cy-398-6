from django.test import TestCase
from rest_framework.test import APIClient
from app.apps.products.models import Product
from app.apps.orders.models import Order, CartItem
from app.apps.notifications.models import Notification


class RefundFlowTestCase(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.seller_id = 1001
        self.buyer_id = 2001
        self.product = Product.objects.create(
            seller_id=self.seller_id,
            name="二手 iPhone 13",
            description="95 新，无划痕",
            original_price=5999.00,
            sale_price=3299.00,
            condition="like_new",
            category="digital",
            images=[],
            weight_kg=0.2,
            is_on_sale=True,
            stock=10,
        )

    def _add_to_cart_and_checkout(self, quantity=2):
        CartItem.objects.create(
            user_id=self.buyer_id,
            product_id=self.product.id,
            quantity=quantity,
        )
        resp = self.client.post(
            "/api/orders/checkout/",
            {"buyer_id": self.buyer_id},
            format="json",
        )
        return resp

    def test_01_checkout_deducts_stock(self):
        initial_stock = Product.objects.get(id=self.product.id).stock
        self.assertEqual(initial_stock, 10)

        resp = self._add_to_cart_and_checkout(quantity=3)
        self.assertEqual(resp.status_code, 200)
        order_id = resp.json()["id"]

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)

        order = Order.objects.get(id=order_id)
        self.assertEqual(order.status, "pending_pay")
        self.assertEqual(order.buyer_id, self.buyer_id)
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0]["quantity"], 3)

    def test_02_checkout_insufficient_stock_rejected(self):
        self.product.stock = 1
        self.product.save()

        CartItem.objects.create(
            user_id=self.buyer_id,
            product_id=self.product.id,
            quantity=5,
        )
        resp = self.client.post(
            "/api/orders/checkout/",
            {"buyer_id": self.buyer_id},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)

    def test_03_buyer_submit_refund_and_seller_approve(self):
        initial_stock = 10
        quantity = 2
        self.assertEqual(self.product.stock, initial_stock)

        resp = self._add_to_cart_and_checkout(quantity=quantity)
        order_id = resp.json()["id"]
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock - quantity)

        order = Order.objects.get(id=order_id)
        order.status = "received"
        order.save()

        Notification.objects.all().delete()

        resp = self.client.post(f"/api/orders/{order_id}/refund/", format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "refund_pending")

        buyer_notices = list(Notification.objects.filter(user_id=self.buyer_id))
        seller_notices = list(Notification.objects.filter(user_id=self.seller_id))
        self.assertEqual(len(buyer_notices), 1)
        self.assertEqual(len(seller_notices), 1)
        self.assertIn("退款申请已提交", buyer_notices[0].title)
        self.assertIn("退款申请通知", seller_notices[0].title)

        Notification.objects.all().delete()

        resp = self.client.post(f"/api/orders/{order_id}/approve_refund/", format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "refunded")

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock)

        buyer_notices = list(Notification.objects.filter(user_id=self.buyer_id))
        seller_notices = list(Notification.objects.filter(user_id=self.seller_id))
        self.assertEqual(len(buyer_notices), 1)
        self.assertEqual(len(seller_notices), 1)
        self.assertIn("退款已通过", buyer_notices[0].title)
        self.assertIn("退款已完成", seller_notices[0].title)

    def test_04_refund_only_allowed_for_received_or_completed(self):
        resp = self._add_to_cart_and_checkout(quantity=1)
        order_id = resp.json()["id"]

        resp = self.client.post(f"/api/orders/{order_id}/refund/", format="json")
        self.assertEqual(resp.status_code, 400)

        order = Order.objects.get(id=order_id)
        order.status = "completed"
        order.save()

        resp = self.client.post(f"/api/orders/{order_id}/refund/", format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "refund_pending")

    def test_05_approve_refund_only_allowed_from_refund_pending(self):
        resp = self._add_to_cart_and_checkout(quantity=1)
        order_id = resp.json()["id"]

        resp = self.client.post(f"/api/orders/{order_id}/approve_refund/", format="json")
        self.assertEqual(resp.status_code, 400)

        order = Order.objects.get(id=order_id)
        order.status = "refund_pending"
        order.save()

        resp = self.client.post(f"/api/orders/{order_id}/approve_refund/", format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "refunded")
