from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

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
        return data

class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
