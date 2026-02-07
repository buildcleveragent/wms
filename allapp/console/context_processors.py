# ===============================
# FILE: allapp/console/context_processors.py
# 说明：把 Ribbon 菜单注入模板上下文
# ===============================
from .menu import build_ribbon

def console_menu(request):
    if not request.user.is_authenticated:
        return {"CONSOLE_MENU": {"tabs": [], "active_index": 0}}
    try:
        data = build_ribbon(request.user, request.path or "")
    except Exception:
        data = {"tabs": [], "active_index": 0}
    return {"CONSOLE_MENU": data}
