from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from app.apps.orders.models import CartItem, Order
from app.apps.orders.serializers import CartItemSerializer, OrderSerializer
from app.apps.products.models import Product
from app.apps.notifications.models import Notification
from app.constants.errors import ERRORS
from app.constants.enums import ORDER_STATUS

class CartViewSet(viewsets.ModelViewSet):
    queryset = CartItem.objects.all()
    serializer_class = CartItemSerializer

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all().order_by("-created_at")
    serializer_class = OrderSerializer

    @action(detail=False, methods=["post"])
    @transaction.atomic
    def checkout(self, request):
        buyer_id = request.data.get("buyer_id")
        cart_items = list(CartItem.objects.filter(user_id=buyer_id).values("product_id", "quantity"))
        if not cart_items:
            raise ValidationError(ERRORS["VALIDATION_FAILED"])
        product_ids = [item["product_id"] for item in cart_items]
        products = Product.objects.filter(id__in=product_ids)
        product_map = {p.id: p for p in products}
        for item in cart_items:
            product = product_map.get(item["product_id"])
            if not product or product.stock < item["quantity"]:
                raise ValidationError(ERRORS["INSUFFICIENT_STOCK"])
        for item in cart_items:
            product = product_map[item["product_id"]]
            product.stock -= item["quantity"]
            product.save(update_fields=["stock"])
        order = Order.objects.create(buyer_id=buyer_id, items=cart_items, total_amount=max(len(cart_items), 1) * 49)
        CartItem.objects.filter(user_id=buyer_id).delete()
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"])
    def status(self, request, pk=None):
        order = self.get_object()
        new_status = request.data.get("status", order.status)
        if new_status not in ORDER_STATUS:
            raise ValidationError(ERRORS["ORDER_STATUS_INVALID"])
        order.status = new_status
        order.save(update_fields=["status"])
        return Response(OrderSerializer(order).data)

    def _get_seller_ids(self, order):
        product_ids = [item["product_id"] for item in order.items]
        sellers = Product.objects.filter(id__in=product_ids).values_list("seller_id", flat=True)
        return list(set(sellers))

    def _create_notification(self, user_id, title, content):
        Notification.objects.create(user_id=user_id, title=title, content=content)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        order = self.get_object()
        if order.status not in ("received", "completed"):
            raise ValidationError(ERRORS["REFUND_NOT_ALLOWED"])
        order.status = "refund_pending"
        order.save(update_fields=["status"])
        seller_ids = self._get_seller_ids(order)
        for seller_id in seller_ids:
            self._create_notification(
                seller_id,
                "退款申请通知",
                f"订单 {order.id} 买家已提交退款申请，请及时处理。"
            )
        self._create_notification(
            order.buyer_id,
            "退款申请已提交",
            f"订单 {order.id} 退款申请已提交，等待卖家审核。"
        )
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def approve_refund(self, request, pk=None):
        order = self.get_object()
        if order.status != "refund_pending":
            raise ValidationError(ERRORS["ORDER_STATUS_INVALID"])
        for item in order.items:
            product = Product.objects.filter(id=item["product_id"]).first()
            if product:
                product.stock += item["quantity"]
                product.save(update_fields=["stock"])
        order.status = "refunded"
        order.save(update_fields=["status"])
        seller_ids = self._get_seller_ids(order)
        for seller_id in seller_ids:
            self._create_notification(
                seller_id,
                "退款已完成",
                f"订单 {order.id} 退款已确认，商品库存已退回。"
            )
        self._create_notification(
            order.buyer_id,
            "退款已通过",
            f"订单 {order.id} 退款申请已通过，退款将原路返回。"
        )
        return Response(OrderSerializer(order).data)
