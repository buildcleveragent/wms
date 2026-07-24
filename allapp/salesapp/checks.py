from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, register


def _payment_error(message, code):
    return Error(message, id=code)


def _key_bytes(value):
    normalized = str(value or "").replace("\\n", "\n")
    if "-----BEGIN " in normalized:
        return normalized.encode("utf-8")
    return Path(normalized).read_bytes()


@register()
def check_wechat_pay_production_configuration(app_configs, **kwargs):
    if not getattr(settings, "IS_PRODUCTION", False):
        return []
    errors = []
    if not getattr(settings, "WECHAT_PAY_VERIFY_CALLBACK_SIGNATURE", False):
        errors.append(
            Error(
                "生产环境必须启用微信支付回调及同步响应验签。",
                id="salesapp.E001",
            )
        )
    platform_keys = getattr(settings, "WECHAT_PAY_PLATFORM_KEYS", {}) or {}
    if not platform_keys:
        errors.append(_payment_error("生产环境必须配置平台公钥序列号映射。", "salesapp.E002"))
    required = [
        "WECHAT_MINI_APPID",
        "WECHAT_PAY_MCH_ID",
        "WECHAT_PAY_MCH_SERIAL_NO",
        "WECHAT_PAY_APIV3_KEY",
        "WECHAT_PAY_NOTIFY_URL",
        "WECHAT_PAY_REFUND_NOTIFY_URL",
    ]
    missing = [name for name in required if not getattr(settings, name, "")]
    if not (
        getattr(settings, "WECHAT_PAY_PRIVATE_KEY", "")
        or getattr(settings, "WECHAT_PAY_PRIVATE_KEY_PATH", "")
    ):
        missing.append("WECHAT_PAY_PRIVATE_KEY/WECHAT_PAY_PRIVATE_KEY_PATH")
    if missing:
        errors.append(
            Error(
                f"生产环境微信支付配置缺失：{', '.join(missing)}。",
                id="salesapp.E003",
            )
        )
        return errors

    if len(settings.WECHAT_PAY_APIV3_KEY.encode("utf-8")) != 32:
        errors.append(
            _payment_error("WECHAT_PAY_APIV3_KEY 必须恰好为 32 字节。", "salesapp.E004")
        )
    for name in ("WECHAT_PAY_NOTIFY_URL", "WECHAT_PAY_REFUND_NOTIFY_URL"):
        parsed = urlparse(getattr(settings, name, ""))
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append(
                _payment_error(f"{name} 必须是公网 HTTPS 地址。", "salesapp.E005")
            )

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        private_value = (
            settings.WECHAT_PAY_PRIVATE_KEY_PATH or settings.WECHAT_PAY_PRIVATE_KEY
        )
        serialization.load_pem_private_key(_key_bytes(private_value), password=None)
        for serial, value in platform_keys.items():
            if not str(serial).strip():
                raise ValueError("平台公钥序列号不能为空")
            key_data = _key_bytes(value)
            if b"BEGIN CERTIFICATE" in key_data:
                x509.load_pem_x509_certificate(key_data).public_key()
            else:
                serialization.load_pem_public_key(key_data)
    except ImportError:
        errors.append(
            _payment_error("生产环境缺少 cryptography 支付依赖。", "salesapp.E006")
        )
    except (OSError, TypeError, ValueError) as exc:
        errors.append(
            _payment_error(f"微信支付密钥无法读取或解析：{exc}", "salesapp.E007")
        )
    return errors
