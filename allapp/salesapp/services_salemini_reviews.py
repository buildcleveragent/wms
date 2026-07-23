from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg, Count, Q

from .models import SaleMiniProductReview

ONE_DECIMAL = Decimal("0.1")


def published_reviews(config):
    return SaleMiniProductReview.objects.filter(
        product_config=config,
        status=SaleMiniProductReview.Status.PUBLISHED,
        is_active=True,
    )


def _average(value):
    return str(Decimal(value or 0).quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP))


def review_summary(config):
    qs = published_reviews(config)
    values = qs.aggregate(
        count=Count("id"),
        average_quality=Avg("quality_score"),
        average_delivery=Avg("delivery_score"),
        average_overall=Avg("overall_score"),
        score_1=Count("id", filter=Q(overall_score=1)),
        score_2=Count("id", filter=Q(overall_score=2)),
        score_3=Count("id", filter=Q(overall_score=3)),
        score_4=Count("id", filter=Q(overall_score=4)),
        score_5=Count("id", filter=Q(overall_score=5)),
    )
    return {
        "count": values["count"],
        "average_quality": _average(values["average_quality"]),
        "average_delivery": _average(values["average_delivery"]),
        "average_overall": _average(values["average_overall"]),
        "score_counts": {str(score): values[f"score_{score}"] for score in range(1, 6)},
        "with_images_count": qs.filter(
            images__is_active=True,
            images__is_deleted=False,
        )
        .distinct()
        .count(),
    }


def _absolute_media_url(request, image):
    try:
        url = image.url
    except (ValueError, AttributeError):
        return ""
    return request.build_absolute_uri(url) if request else url


def public_review_payload(request, review):
    anonymous = review.is_anonymous
    buyer = review.buyer_user
    images = [
        {
            "id": row.id,
            "url": _absolute_media_url(request, row.image),
            "width": row.width,
            "height": row.height,
        }
        for row in review.images.all()
        if row.is_active and not row.is_deleted
    ]
    return {
        "id": review.id,
        "quality_score": review.quality_score,
        "delivery_score": review.delivery_score,
        "overall_score": review.overall_score,
        "content": review.content,
        "is_anonymous": anonymous,
        "display_name": "匿名用户" if anonymous else (buyer.nickname or "商城用户"),
        "avatar_url": "" if anonymous else buyer.avatar_url,
        "verified_purchase": True,
        "images": images,
        "published_at": (
            review.published_at.isoformat() if review.published_at else ""
        ),
    }


def private_review_payload(request, review):
    payload = public_review_payload(request, review)
    payload.update(
        {
            "order_line_id": review.order_line_id,
            "product_id": review.product_id,
            "config_id": review.product_config_id,
            "product_name": review.product.name,
            "product_spec": review.product.spec or "",
            "product_image_url": "",
            "status": review.status,
            "status_name": review.get_status_display(),
            "rejection_reason": review.rejection_reason,
            "submitted_at": (
                review.submitted_at.isoformat() if review.submitted_at else ""
            ),
        }
    )
    try:
        product_image = review.product.product_image
        payload["product_image_url"] = _absolute_media_url(request, product_image)
    except (AttributeError, ValueError):
        pass
    return payload
