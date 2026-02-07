from rest_framework.permissions import IsAuthenticated, SAFE_METHODS

class IsStaffOrReadOnly(IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return True if request.method in SAFE_METHODS else bool(request.user and request.user.is_staff)
