# allapp/baseinfo/admin.py
from django.contrib import admin

from allapp.core.admin_mixins import HideAuditFieldsMixin
from allapp.core.admin_base import BaseReadonlyAdmin
from .models import Owner,Customer, Employee, CarrierCompany, Supplier, Driver, Vehicle, Route, DictCategory, DictItem

# ========== Owner ==========
@admin.register(Owner)
class OwnerAdmin(HideAuditFieldsMixin,BaseReadonlyAdmin):
    model = "Owner"
    admin_priority = 1
    fields = ("name","code","contact_person","phone","sms_mobile","bank_account","wx","qq","email","business_license","tax_registration","organization_code")
    list_display = ("name", "code", "contact_person", "phone",)
    search_fields = ("name", "code", "contact_person", "phone", "email")
    list_filter = ("name", "code", "contact_person")

    class Media:
        css = {
            'all': ('css/admin/owner_admin.css',)  # 引入你自定义的 CSS 文件
        }

# --- Customer Admin ---
@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'owner', 'salesperson', 'area', 'delivery_route', 'delivery_seq', 'level', 'email',
                    'mobile')
    search_fields = ['code', 'name',]
    list_filter = ['owner', 'salesperson', 'area', 'delivery_route']
    ordering = ['owner', 'code']
    list_per_page = 20  # Custom pagination

    fields = (
        'owner', 'code', 'name', 'salesperson',
        'contact_person', 'phone', 'mobile', 'qq', 'email',
        'area', 'delivery_route', 'delivery_seq', 'level',
        'bank_name', 'bank_account', 'delivery_distance_km', 'promised_days', 'external_code'
    )

# --- Employee Admin ---
@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'gender', 'department', 'position', 'mobile', 'email', )
    search_fields = ['code', 'name', 'department', 'position']
    list_filter = ['department', 'position', ]
    ordering = ['code']

    fields = (
        'code', 'name', 'gender', 'phone', 'mobile', 'email',
        'department', 'position', 'hire_date', 'leave_date',
        'birthday', 'education', 'id_number', 'bank_name', 'bank_account', 'address',
    )


# --- CarrierCompany Admin ---
@admin.register(CarrierCompany)
class CarrierCompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'manager', 'mobile', 'phone', 'owner')
    search_fields = ['name', 'manager', 'owner__name']
    list_filter = ['owner']
    ordering = ['name']

    fields = (
        'name', 'manager', 'owner',
        'mobile', 'phone'
    )


# --- Supplier Admin ---
@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'contact_person', 'phone', 'email')
    search_fields = ['name', 'contact_person', 'phone', 'owner__name']
    list_filter = ['owner']
    ordering = ['name']

    fields = (
        'owner', 'name', 'contact_person', 'phone', 'email', 'qq', 'yb', 'bank_account'
    )


# --- Driver Admin ---
@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ('name', 'carrier_company', 'gender', 'mobile', 'phone', 'id_number', 'driver_license_no')
    search_fields = ['name',  'mobile', 'id_number']
    list_filter = ['carrier_company']
    ordering = ['name']

    fields = (
        'name', 'carrier_company', 'gender',
        'mobile', 'phone', 'id_number', 'driver_license_no', 'driver_license_expiry'
    )


# --- Vehicle Admin ---
@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('plate_no', 'carrier_company', 'use_type', 'model_name', 'vin', 'status', 'driver')
    search_fields = ['plate_no', 'driver__name']
    list_filter = ['carrier_company', 'status']
    ordering = ['plate_no']

    fields = (
        'plate_no', 'carrier_company', 'use_type', 'model_name', 'category',
        'vin', 'license_no', 'engine_no', 'trailer_no',
        'insurance_no', 'operation_permit_no', 'surcharge_cert_no',
        'payload_kg', 'length_m', 'volume_m3', 'maintenance_km',
        'status', 'driver', 'warranty_expiry', 'maintenance_due', 'operation_permit_annual_due', 'annual_inspection_due', 'remark'
    )


# --- Route Admin ---
@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ['code', 'name']
    ordering = ['code']

    fields = ('code', 'name', 'remark')


# --- DictCategory Admin ---
@admin.register(DictCategory)
class DictCategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_locked')
    search_fields = ['code', 'name']
    ordering = ['code']

    fields = ('code', 'name', 'is_locked')


# --- DictItem Admin ---
@admin.register(DictItem)
class DictItemAdmin(admin.ModelAdmin):
    list_display = ('category', 'code', 'name', 'value', 'sort_order')
    search_fields = ['code', 'name', 'category__name']
    list_filter = ['category']
    ordering = ['category', 'sort_order']

    fields = (
        'category', 'code', 'name', 'value', 'extra', 'sort_order'
    )

