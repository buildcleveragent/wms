from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    SaleMiniOrderMapping,
    SaleMiniProductReview,
    SaleMiniProductReviewImage,
    SaleProductConfig,
)
from .salemini_api import _buyer_bindings_for_user, _public_config_qs
from .services_salemini_reviews import (
    private_review_payload,
    public_review_payload,
    published_reviews,
    review_summary,
)

MAX_REVIEW_IMAGES = 6
MAX_REVIEW_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
EDITABLE_REVIEW_STATUSES = {
    SaleMiniProductReview.Status.DRAFT,
    SaleMiniProductReview.Status.REJECTED,
}


class SaleMiniReviewPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 50


class SaleMiniReviewDraftSerializer(serializers.Serializer):
    order_line_id = serializers.IntegerField(min_value=1)
    quality_score = serializers.IntegerField(min_value=1, max_value=5, default=5)
    delivery_score = serializers.IntegerField(min_value=1, max_value=5, default=5)
    overall_score = serializers.IntegerField(min_value=1, max_value=5, default=5)
    content = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=1000
    )
    is_anonymous = serializers.BooleanField(default=True)


class SaleMiniReviewUpdateSerializer(serializers.Serializer):
    quality_score = serializers.IntegerField(min_value=1, max_value=5)
    delivery_score = serializers.IntegerField(min_value=1, max_value=5)
    overall_score = serializers.IntegerField(min_value=1, max_value=5)
    content = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=1000
    )
    is_anonymous = serializers.BooleanField(default=True)


def _bindings_for_request(request):
    bindings = list(_buyer_bindings_for_user(request.user))
    if not bindings:
        raise PermissionDenied("当前账号没有商城购买记录。")
    return bindings


def _mapping_and_line_for_request(request, order_line_id, *, for_update=False):
    bindings = _bindings_for_request(request)
    qs = SaleMiniOrderMapping.objects.filter(
        buyer_user__in=bindings,
        outbound_order__lines__id=order_line_id,
        outbound_order__lines__is_deleted=False,
    ).select_related("buyer_user", "outbound_order")
    if for_update:
        qs = qs.select_for_update()
    mapping = qs.first()
    if not mapping:
        raise PermissionDenied("只能评价本人购买过的商品。")
    line = (
        mapping.outbound_order.lines.filter(id=order_line_id, is_deleted=False)
        .select_related("product")
        .first()
    )
    if not line:
        raise PermissionDenied("订单商品不存在或不可评价。")
    return mapping, line


def _validate_review_eligibility(mapping):
    order = mapping.outbound_order
    if not order.is_closed:
        raise ValidationError({"status": "订单完成后才能评价商品。"})
    if mapping.payment_status not in {
        SaleMiniOrderMapping.PaymentStatus.PAID,
        SaleMiniOrderMapping.PaymentStatus.OFFLINE,
    }:
        raise ValidationError({"payment": "当前订单付款状态不允许评价。"})


def _review_for_request(request, pk, *, for_update=False):
    bindings = _bindings_for_request(request)
    qs = SaleMiniProductReview.objects.filter(
        pk=pk, buyer_user__in=bindings
    ).select_related("buyer_user", "product", "mapping", "order_line")
    if for_update:
        qs = qs.select_for_update()
    return get_object_or_404(qs, pk=pk)


def _ensure_editable(review):
    if review.status not in EDITABLE_REVIEW_STATUSES:
        raise ValidationError({"status": "当前评价状态不能修改。"})


def _apply_review_data(review, data, user):
    review.quality_score = data["quality_score"]
    review.delivery_score = data["delivery_score"]
    review.overall_score = data["overall_score"]
    review.content = (data.get("content") or "").strip()
    review.is_anonymous = data.get("is_anonymous", True)
    review.status = SaleMiniProductReview.Status.DRAFT
    review.rejection_reason = ""
    review.updated_by = user
    try:
        review.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict) from exc
    review.save()


def _validate_uploaded_image(uploaded):
    if not uploaded:
        raise ValidationError({"image": "请选择要上传的图片。"})
    if uploaded.size > MAX_REVIEW_IMAGE_BYTES:
        raise ValidationError({"image": "单张图片不能超过 5MB。"})
    try:
        uploaded.seek(0)
        with Image.open(uploaded) as source:
            image_format = source.format
            source.verify()
        if image_format not in ALLOWED_IMAGE_FORMATS:
            raise ValidationError({"image": "仅支持 JPEG、PNG、WebP 图片。"})
        uploaded.seek(0)
        with Image.open(uploaded) as source:
            width, height = source.size
        if width <= 0 or height <= 0:
            raise ValidationError({"image": "图片尺寸无效。"})
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValidationError({"image": "图片文件损坏或格式不受支持。"}) from exc
    uploaded.seek(0)
    uploaded.name = f"review{ALLOWED_IMAGE_FORMATS[image_format]}"
    return width, height


class SaleMiniProductReviewListApi(APIView):
    permission_classes = [AllowAny]
    pagination_class = SaleMiniReviewPagination

    def get(self, request, pk):
        config_id = request.query_params.get("config_id")
        configs = _public_config_qs().filter(product_id=pk)
        if config_id:
            configs = configs.filter(id=config_id)
        config = get_object_or_404(configs.order_by("sort_order", "id"))
        qs = (
            published_reviews(config)
            .select_related("buyer_user")
            .prefetch_related("images")
        )
        score = request.query_params.get("score")
        if score:
            try:
                score_value = int(score)
            except (TypeError, ValueError) as exc:
                raise ValidationError({"score": "评分筛选必须是 1 至 5。"}) from exc
            if score_value not in range(1, 6):
                raise ValidationError({"score": "评分筛选必须是 1 至 5。"})
            qs = qs.filter(overall_score=score_value)
        if request.query_params.get("has_images") in {"1", "true", "True"}:
            qs = qs.filter(images__is_active=True, images__is_deleted=False).distinct()
        ordering = request.query_params.get("ordering") or "newest"
        orderings = {
            "newest": ("-published_at", "-id"),
            "highest": ("-overall_score", "-published_at", "-id"),
            "lowest": ("overall_score", "-published_at", "-id"),
        }
        if ordering not in orderings:
            raise ValidationError({"ordering": "排序仅支持 newest、highest、lowest。"})
        qs = qs.order_by(*orderings[ordering])
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        response = paginator.get_paginated_response(
            [public_review_payload(request, row) for row in page]
        )
        response.data["summary"] = review_summary(config)
        return response


class SaleMiniReviewDraftCreateApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniReviewDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        mapping, line = _mapping_and_line_for_request(
            request, data["order_line_id"], for_update=True
        )
        _validate_review_eligibility(mapping)
        config = get_object_or_404(
            SaleProductConfig.objects.filter(
                owner_id=mapping.owner_id,
                product_id=line.product_id,
            ).order_by("id")
        )
        try:
            review, created = SaleMiniProductReview.objects.get_or_create(
                order_line=line,
                defaults={
                    "owner": mapping.owner,
                    "customer": mapping.customer,
                    "buyer_user": mapping.buyer_user,
                    "mapping": mapping,
                    "product": line.product,
                    "product_config": config,
                    "created_by": request.user,
                    "updated_by": request.user,
                },
            )
        except IntegrityError:
            review = SaleMiniProductReview.objects.select_for_update().get(
                order_line=line
            )
            created = False
        if not created:
            if review.buyer_user_id != mapping.buyer_user_id:
                raise PermissionDenied("评价归属异常。")
            _ensure_editable(review)
        _apply_review_data(review, data, request.user)
        review = SaleMiniProductReview.objects.prefetch_related("images").get(
            pk=review.pk
        )
        return Response(
            private_review_payload(request, review),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SaleMiniReviewDraftDetailApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        review = _review_for_request(request, pk)
        return Response(private_review_payload(request, review))

    @transaction.atomic
    def put(self, request, pk):
        serializer = SaleMiniReviewUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = _review_for_request(request, pk, for_update=True)
        _ensure_editable(review)
        _validate_review_eligibility(review.mapping)
        _apply_review_data(review, serializer.validated_data, request.user)
        return Response(private_review_payload(request, review))


class SaleMiniReviewImageCreateApi(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request, pk):
        review = _review_for_request(request, pk, for_update=True)
        _ensure_editable(review)
        _validate_review_eligibility(review.mapping)
        active_images = list(
            review.images.filter(is_active=True, is_deleted=False).order_by(
                "sort_order"
            )
        )
        if len(active_images) >= MAX_REVIEW_IMAGES:
            raise ValidationError({"image": "每条评价最多上传 6 张图片。"})
        uploaded = request.FILES.get("image")
        width, height = _validate_uploaded_image(uploaded)
        used_orders = {row.sort_order for row in active_images}
        sort_order = next(
            index for index in range(MAX_REVIEW_IMAGES) if index not in used_orders
        )
        row = SaleMiniProductReviewImage(
            review=review,
            image=uploaded,
            sort_order=sort_order,
            size_bytes=uploaded.size,
            width=width,
            height=height,
            created_by=request.user,
            updated_by=request.user,
        )
        try:
            row.save()
        except Exception:
            if row.image.name:
                row.image.storage.delete(row.image.name)
            raise
        return Response(
            private_review_payload(
                request,
                SaleMiniProductReview.objects.prefetch_related("images").get(
                    pk=review.pk
                ),
            ),
            status=status.HTTP_201_CREATED,
        )


class SaleMiniReviewImageDeleteApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def delete(self, request, pk, image_id):
        review = _review_for_request(request, pk, for_update=True)
        _ensure_editable(review)
        row = get_object_or_404(
            SaleMiniProductReviewImage.objects.filter(review=review), pk=image_id
        )
        storage = row.image.storage
        name = row.image.name
        row.delete()
        if name:
            transaction.on_commit(lambda: storage.delete(name))
        return Response(status=status.HTTP_204_NO_CONTENT)


class SaleMiniReviewSubmitApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        review = _review_for_request(request, pk, for_update=True)
        _ensure_editable(review)
        _validate_review_eligibility(review.mapping)
        try:
            review.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict) from exc
        review.status = SaleMiniProductReview.Status.PENDING
        review.submitted_at = timezone.now()
        review.rejection_reason = ""
        review.updated_by = request.user
        review.save(
            update_fields=[
                "status",
                "submitted_at",
                "rejection_reason",
                "updated_by",
                "updated_at",
            ]
        )
        return Response(private_review_payload(request, review))
