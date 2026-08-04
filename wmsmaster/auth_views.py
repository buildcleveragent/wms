import logging

from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import get_md5_hash_password
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView as BaseTokenRefreshView,
)

from allapp.accounts.audit import record_audit_event

logger = logging.getLogger(__name__)


class LoginSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # 可按需往 token 加自定义字段：
        # token["username"] = user.username
        return token

    def validate(self, attrs):
        data = super().validate(attrs)  # 标准返回里本应就含 access/refresh
        # 兜底：万一被别处覆盖了，强制补齐
        if "refresh" not in data or "access" not in data:
            refresh = self.get_token(self.user)
            data["refresh"] = str(refresh)
            data["access"] = str(refresh.access_token)
        # 顺便回传点基础用户信息
        data["user"] = {"id": self.user.id, "username": self.user.username}
        try:
            record_audit_event(
                action="LOGIN",
                module="authentication",
                request=self.context.get("request"),
                user=self.user,
            )
        except Exception:
            logger.exception("audit.login.write_failed user_id=%s", self.user.id)
        return data


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer


class PasswordRevokingTokenRefreshSerializer(TokenRefreshSerializer):
    """Reject refresh tokens issued before the user's latest password hash."""

    def validate(self, attrs):
        token = RefreshToken(attrs["refresh"])
        user_id = token.get("user_id")
        user = (
            get_user_model()
            .objects.filter(pk=user_id, is_active=True)
            .only("password")
            .first()
        )
        if not user or token.get("hash_password") != get_md5_hash_password(
            user.password
        ):
            raise AuthenticationFailed(
                "令牌已因密码变更而失效。", code="password_changed"
            )
        return super().validate(attrs)


class TokenRefreshView(BaseTokenRefreshView):
    serializer_class = PasswordRevokingTokenRefreshSerializer
