# allapp/baseinfo/admin.py
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.urls import path, reverse

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.core.admin_base import BaseReadonlyAdmin
from allapp.core.admin_mixins import HideAuditFieldsMixin, HideAuditInlineMixin
from allapp.core.admin_tenant import (
    TenantScopedAdminMixin,
    TenantScopedInlineMixin,
    scope_admin_queryset,
)

from .models import (
    CarrierCompany,
    Customer,
    DictCategory,
    DictItem,
    Driver,
    Employee,
    Owner,
    OwnerCustomerBinding,
    OwnerWarehouseBinding,
    Route,
    Supplier,
    Vehicle,
)


class OwnerWarehouseBindingInline(
    TenantScopedInlineMixin, HideAuditInlineMixin, admin.TabularInline
):
    model = OwnerWarehouseBinding
    fields = ("warehouse", "is_active", "remark")
    autocomplete_fields = ("warehouse",)
    extra = 0


# ========== Owner ==========
@admin.register(Owner)
class OwnerAdmin(TenantScopedAdminMixin, HideAuditFieldsMixin, BaseReadonlyAdmin):
    model = "Owner"
    admin_priority = 1
    fields = (
        "name",
        "code",
        "next_sku_sequence",
        "contact_person",
        "phone",
        "sms_mobile",
        "bank_account",
        "wx",
        "qq",
        "email",
        "business_license",
        "tax_registration",
        "organization_code",
        "allow_warehouse_assisted_outbound",
    )
    list_display = (
        "name",
        "code",
        "next_sku_sequence",
        "contact_person",
        "phone",
        "allow_warehouse_assisted_outbound",
    )
    search_fields = ("name", "code", "contact_person", "phone", "email")
    list_filter = ("allow_warehouse_assisted_outbound", "name", "code", "contact_person")
    readonly_fields = ("next_sku_sequence",)
    inlines = (OwnerWarehouseBindingInline,)

    class Media:
        css = {"all": ("css/admin/owner_admin.css",)}  # 引入你自定义的 CSS 文件


# --- Customer Admin ---
class OwnerCustomerBindingInline(TenantScopedInlineMixin, admin.TabularInline):
    model = OwnerCustomerBinding
    fields = (
        "owner",
        "code",
        "external_code",
        "salesperson",
        "area",
        "delivery_route",
        "delivery_seq",
        "level",
        "delivery_distance_km",
        "promised_days",
        "is_active",
    )
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return bool(obj and AccessScope.for_user(request.user).is_global)

    def get_readonly_fields(self, request, obj=None):
        return () if AccessScope.for_user(request.user).is_global else ("owner",)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) or request.user.has_perm(
            "baseinfo.view_customer"
        )

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) or request.user.has_perm(
            "baseinfo.change_customer"
        )


@admin.register(Customer)
class CustomerAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    inlines = (OwnerCustomerBindingInline,)

    class Media:
        js = ("admin/customer_owner_linkage.js",)

    common_fields = (
        "name",
        "contact_person",
        "phone",
        "mobile",
        "qq",
        "email",
        "province",
        "city",
        "district",
        "street",
        "address",
        "postal_code",
        "bank_name",
        "bank_account",
        "is_active",
    )

    def get_form(self, request, obj=None, **kwargs):
        base_form = super().get_form(request, obj, **kwargs)
        model_admin = self

        class CustomerForm(base_form):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                if "salesperson" not in self.fields:
                    return
                owner_id = (
                    self.data.get(self.add_prefix("owner"))
                    if self.is_bound
                    else self.initial.get("owner")
                )
                field = self.fields["salesperson"]
                field.queryset = model_admin._salesperson_queryset(request, owner_id)
                field.error_messages["invalid_choice"] = "请选择当前货主的业务员。"
                widget = getattr(field.widget, "widget", field.widget)
                widget.attrs["data-source-url"] = reverse(
                    "admin:baseinfo_customer_salesperson_options",
                    current_app=model_admin.admin_site.name,
                )

            def clean(self):
                data = super().clean()
                if obj is None and data.get("owner") and data.get("code"):
                    if OwnerCustomerBinding.all_objects.filter(
                        owner=data["owner"], code=data["code"].strip()
                    ).exists():
                        self.add_error("code", "该货主下的客户编号已存在。")
                return data

        return CustomerForm

    def _salesperson_queryset(self, request, owner_id):
        """Use active salesperson role scopes within an accessible selected owner."""
        users = get_user_model().objects.all()
        try:
            owner_id = int(getattr(owner_id, "pk", owner_id))
        except (TypeError, ValueError):
            return users.none()
        owners = scope_admin_queryset(request.user, Owner.objects.all(), for_write=True)
        if not owners.filter(pk=owner_id).exists():
            return users.none()
        return (
            users.filter(
                is_active=True,
                role_scopes__role=UserRoleScope.Role.OWNER_SALESPERSON,
                role_scopes__owner_id=owner_id,
                role_scopes__is_active=True,
            )
            .order_by("username", "pk")
            .distinct()
        )

    def get_urls(self):
        return [
            path(
                "salesperson-options/",
                self.admin_site.admin_view(self.salesperson_options_view),
                name="baseinfo_customer_salesperson_options",
            ),
        ] + super().get_urls()

    def salesperson_options_view(self, request):
        if not (self.has_add_permission(request) or self.has_change_permission(request)):
            raise PermissionDenied
        users = self._salesperson_queryset(request, request.GET.get("owner"))
        return JsonResponse({"results": [{"id": user.pk, "text": str(user)} for user in users]})

    def get_fields(self, request, obj=None):
        if obj:
            return self.common_fields
        return ("owner", "code", "salesperson", *self.common_fields)

    def get_readonly_fields(self, request, obj=None):
        if obj and not AccessScope.for_user(request.user).is_global:
            return self.common_fields
        return ()

    def has_delete_permission(self, request, obj=None):
        if not AccessScope.for_user(request.user).is_global:
            return False
        return super().has_delete_permission(request, obj)

    def get_list_display(self, request):
        return ("name", "contact_person", "phone", "mobile", "is_active")

    def get_list_filter(self, request):
        return ("is_active",)

    def get_search_fields(self, request):
        return ("name", "phone", "mobile")

    def get_ordering(self, request):
        return ("name", "id")

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        if not change:
            obj.created_by = request.user
        return super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        for inline in formset.forms:
            data = getattr(inline, "cleaned_data", {}) or {}
            if not data or not inline.has_changed():
                continue
            inline.instance.updated_by = request.user
            if not inline.instance.pk:
                inline.instance.created_by = request.user
            if not AccessScope.for_user(request.user).is_global:
                original = OwnerCustomerBinding.objects.filter(pk=inline.instance.pk).first()
                if not original or original.owner_id != inline.instance.owner_id:
                    raise PermissionDenied("跨货主客户关联只能由系统管理员维护。")
        return super().save_formset(request, form, formset, change)

    list_display = (
        "code",
        "name",
        "owner",
        "salesperson",
        "area",
        "delivery_route",
        "delivery_seq",
        "level",
        "email",
        "mobile",
    )
    search_fields = [
        "code",
        "name",
    ]
    list_filter = ["owner", "salesperson", "area", "delivery_route"]
    ordering = ["name", "id"]
    list_per_page = 20  # Custom pagination

    fields = (
        "owner",
        "code",
        "name",
        "salesperson",
        "contact_person",
        "phone",
        "mobile",
        "qq",
        "email",
        "area",
        "delivery_route",
        "delivery_seq",
        "level",
        "bank_name",
        "bank_account",
        "delivery_distance_km",
        "promised_days",
        "external_code",
    )


# --- Employee Admin ---
@admin.register(Employee)
class EmployeeAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "warehouse",
        "gender",
        "department",
        "position",
        "mobile",
        "email",
    )
    search_fields = ["code", "name", "department", "position"]
    list_filter = [
        "warehouse",
        "department",
        "position",
    ]
    ordering = ["code"]

    fields = (
        "code",
        "name",
        "warehouse",
        "gender",
        "phone",
        "mobile",
        "email",
        "department",
        "position",
        "hire_date",
        "leave_date",
        "birthday",
        "education",
        "id_number",
        "bank_name",
        "bank_account",
        "address",
    )


# --- CarrierCompany Admin ---
@admin.register(CarrierCompany)
class CarrierCompanyAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "manager", "warehouse", "mobile", "phone", "owner")
    search_fields = ["name", "manager", "owner__name"]
    list_filter = ["warehouse", "owner"]
    ordering = ["name"]

    fields = ("name", "manager", "warehouse", "owner", "mobile", "phone")


# --- Supplier Admin ---
@admin.register(Supplier)
class SupplierAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "owner", "contact_person", "phone", "email")
    search_fields = ["name", "contact_person", "phone", "owner__name"]
    list_filter = ["owner"]
    ordering = ["name"]

    fields = ("owner", "name", "contact_person", "phone", "email", "qq", "yb", "bank_account")


# --- Driver Admin ---
@admin.register(Driver)
class DriverAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "carrier_company",
        "gender",
        "mobile",
        "phone",
        "id_number",
        "driver_license_no",
    )
    search_fields = ["name", "mobile", "id_number"]
    list_filter = ["carrier_company"]
    ordering = ["name"]

    fields = (
        "name",
        "carrier_company",
        "gender",
        "mobile",
        "phone",
        "id_number",
        "driver_license_no",
        "driver_license_expiry",
    )


# --- Vehicle Admin ---
@admin.register(Vehicle)
class VehicleAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "plate_no",
        "carrier_company",
        "use_type",
        "model_name",
        "vin",
        "status",
        "driver",
    )
    search_fields = ["plate_no", "driver__name"]
    list_filter = ["carrier_company", "status"]
    ordering = ["plate_no"]

    fields = (
        "plate_no",
        "carrier_company",
        "use_type",
        "model_name",
        "category",
        "vin",
        "license_no",
        "engine_no",
        "trailer_no",
        "insurance_no",
        "operation_permit_no",
        "surcharge_cert_no",
        "payload_kg",
        "length_m",
        "volume_m3",
        "maintenance_km",
        "status",
        "driver",
        "warranty_expiry",
        "maintenance_due",
        "operation_permit_annual_due",
        "annual_inspection_due",
        "remark",
    )


# --- Route Admin ---
@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ["code", "name"]
    ordering = ["code"]

    fields = ("code", "name", "remark")


# --- DictCategory Admin ---
@admin.register(DictCategory)
class DictCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_locked")
    search_fields = ["code", "name"]
    ordering = ["code"]

    fields = ("code", "name", "is_locked")


# --- DictItem Admin ---
@admin.register(DictItem)
class DictItemAdmin(admin.ModelAdmin):
    list_display = ("category", "code", "name", "value", "sort_order")
    search_fields = ["code", "name", "category__name"]
    list_filter = ["category"]
    ordering = ["category", "sort_order"]

    fields = ("category", "code", "name", "value", "extra", "sort_order")
