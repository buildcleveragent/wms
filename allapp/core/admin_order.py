# core/admin_order.py
from django.contrib.admin import AdminSite

# 1) 你期望的“应用顺序”
APP_ORDER = [
    "tasking",  # 任务管理
    "inbound",     # 入库管理
    "outbound",    # 出库管理
    "inventory",  # 库存管理
    "products",    # 商品管理
    "baseinfo",    # 基础信息
    "locations",  # 仓位管理
    "billing",     # 计费结算
    "reports",     # 报表
    "auth", "accounts",  # 其它
]
APP_POS = {label: i for i, label in enumerate(APP_ORDER)}

# 2) 每个应用内“模型顺序”
MODEL_ORDER = {
    "locations": ["Subwarehouse", "Location", "Container",],
    "tasking":   ["WmsTask", "WmsTaskLine", "TaskAssignment","TaskScanLog","TaskStatusLog"],
    "inbound":   ["InboundOrder", "InboundOrderLine", "InboundReceipt", "InboundReceiptLine"],
    "outbound":  ["OutboundOrder", "OutboundOrderLine"],
    "inventory": ["InventoryDetail", "InventoryTransaction", "InventorySummary", "PostingJournal"],
    # 其余没列到的按名称字母序靠后
}
def model_pos(app_label, object_name):
    seq = MODEL_ORDER.get(app_label, [])
    try:
        return seq.index(object_name)
    except ValueError:
        return 999

# 3) 包装原始 get_app_list

# def _custom_get_app_list(self, request):
#     app_list = list(_original_get_app_list(self, request))
#
#     # 先排应用
#     app_list.sort(
#         key=lambda a: (APP_POS.get(a.get("app_label"), 999), a.get("name", ""))
#     )
#
#     # 再排每个应用下的模型
#     for app in app_list:
#         models = list(app.get("models", []))
#         app_label = app.get("app_label")
#         models.sort(
#             key=lambda m: (model_pos(app_label, m.get("object_name")), m.get("name", ""))
#         )
#         app["models"] = models
#
#     return app_list

# 4) 打补丁：替换 AdminSite.get_app_list

# def _custom_get_app_list(self, request, app_label):
#     app_list = list(_original_get_app_list(self, request))
#
#     # 排序应用
#     app_list.sort(
#         key=lambda a: (APP_POS.get(a.get("app_label"), 999), a.get("name", ""))
#     )
#
#     # 排序每个应用下的模型
#     for app in app_list:
#         models = list(app.get("models", []))
#         models.sort(
#             key=lambda m: (model_pos(app.get("app_label"), m.get("object_name")), m.get("name", ""))
#         )
#         app["models"] = models
#
#     return app_list

def _custom_get_app_list(self, request, app_label=None):
    # Ensure _original_get_app_list is correctly referencing the original method
    app_list = list(_original_get_app_list(self, request))

    # Sort the applications first
    app_list.sort(
        key=lambda a: (APP_POS.get(a.get("app_label"), 999), a.get("name", ""))
    )

    # Sort the models within each app
    for app in app_list:
        models = list(app.get("models", []))
        # Ensure app.get("app_label") is correctly used
        app_label = app.get("app_label", None)
        models.sort(
            key=lambda m: (model_pos(app_label, m.get("object_name")), m.get("name", ""))
        )
        app["models"] = models

    return app_list

# Save the original get_app_list method
_original_get_app_list = AdminSite.get_app_list
# Override AdminSite.get_app_list with your custom method
AdminSite.get_app_list = _custom_get_app_list
