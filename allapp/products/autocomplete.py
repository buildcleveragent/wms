from dal import autocomplete
from django.db.models import Q
from .models import ProductUom


class ProductUomAutocomplete(autocomplete.Select2QuerySetView):
    def get_queryset(self):
        qs = ProductUom.objects.filter(is_active=True).only("id", "code", "name")
        if self.forwarded.get("only_count") in ("1", 1, True):
            qs = qs.filter(kind="COUNT")
        if self.q:
            qs = qs.filter(Q(code__icontains=self.q) | Q(name__icontains=self.q))
        return qs.order_by("code")
