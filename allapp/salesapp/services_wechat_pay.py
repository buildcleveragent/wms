import base64
import json
import secrets
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib import error as url_error
from urllib import request as url_request

from django.conf import settings


class WechatPayConfigError(Exception):
    pass


class WechatPayRequestError(Exception):
    pass


def money_to_cents(amount) -> int:
    value = Decimal(amount or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _require_crypto():
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise WechatPayConfigError(
            "微信支付需要安装 cryptography 才能完成签名和回调解密。"
        ) from exc
    return x509, InvalidSignature, hashes, serialization, padding, AESGCM


def _private_key_pem() -> bytes:
    if settings.WECHAT_PAY_PRIVATE_KEY_PATH:
        return Path(settings.WECHAT_PAY_PRIVATE_KEY_PATH).read_bytes()
    value = settings.WECHAT_PAY_PRIVATE_KEY
    if not value:
        raise WechatPayConfigError("WECHAT_PAY_PRIVATE_KEY 或路径未配置。")
    return value.replace("\\n", "\n").encode("utf-8")


def _platform_public_key_pem() -> bytes:
    if settings.WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH:
        return Path(settings.WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH).read_bytes()
    value = settings.WECHAT_PAY_PLATFORM_PUBLIC_KEY
    if not value:
        raise WechatPayConfigError("微信支付平台公钥/证书未配置，无法验签回调。")
    return value.replace("\\n", "\n").encode("utf-8")


def _ensure_base_config():
    missing = []
    for name in [
        "WECHAT_MINI_APPID",
        "WECHAT_PAY_MCH_ID",
        "WECHAT_PAY_MCH_SERIAL_NO",
        "WECHAT_PAY_APIV3_KEY",
    ]:
        if not getattr(settings, name, ""):
            missing.append(name)
    if missing:
        raise WechatPayConfigError(f"微信支付配置缺失：{', '.join(missing)}。")
    if len(settings.WECHAT_PAY_APIV3_KEY.encode("utf-8")) != 32:
        raise WechatPayConfigError("WECHAT_PAY_APIV3_KEY 必须是 32 字节。")


def _sign(message: str) -> str:
    _x509, _invalid, hashes, serialization, padding, _aes = _require_crypto()
    private_key = serialization.load_pem_private_key(_private_key_pem(), password=None)
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def _json_body(payload) -> str:
    if payload is None:
        return ""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _authorization_header(
    method: str, path: str, body: str, timestamp: str, nonce: str
):
    message = "\n".join([method.upper(), path, timestamp, nonce, body]) + "\n"
    signature = _sign(message)
    return (
        "WECHATPAY2-SHA256-RSA2048 "
        f'mchid="{settings.WECHAT_PAY_MCH_ID}",'
        f'nonce_str="{nonce}",'
        f'signature="{signature}",'
        f'timestamp="{timestamp}",'
        f'serial_no="{settings.WECHAT_PAY_MCH_SERIAL_NO}"'
    )


def _wechat_request(method: str, path: str, payload=None):
    _ensure_base_config()
    body = _json_body(payload)
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    data = body.encode("utf-8") if method.upper() != "GET" else None
    request = url_request.Request(
        f"{settings.WECHAT_PAY_API_BASE_URL}{path}",
        data=data,
        method=method.upper(),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": _authorization_header(
                method, path, body, timestamp, nonce
            ),
            "User-Agent": "wms-sale-mini/1.0",
        },
    )
    try:
        with url_request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except url_error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise WechatPayRequestError(f"微信支付请求失败：HTTP {exc.code} {raw}") from exc
    except (OSError, ValueError, url_error.URLError) as exc:
        raise WechatPayRequestError("微信支付服务请求失败，请稍后重试。") from exc


def sign_jsapi_pay_params(prepay_id: str):
    appid = settings.WECHAT_MINI_APPID
    if not appid:
        raise WechatPayConfigError("WECHAT_MINI_APPID 未配置。")
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    package = f"prepay_id={prepay_id}"
    message = "\n".join([appid, timestamp, nonce, package]) + "\n"
    return {
        "appId": appid,
        "timeStamp": timestamp,
        "nonceStr": nonce,
        "package": package,
        "signType": "RSA",
        "paySign": _sign(message),
    }


def create_jsapi_prepay(payment, openid: str, description: str):
    notify_url = settings.WECHAT_PAY_NOTIFY_URL
    if not notify_url:
        raise WechatPayConfigError("WECHAT_PAY_NOTIFY_URL 未配置。")
    payload = {
        "appid": settings.WECHAT_MINI_APPID,
        "mchid": settings.WECHAT_PAY_MCH_ID,
        "description": description[:127] or "销售小程序订单",
        "out_trade_no": payment.out_trade_no,
        "notify_url": notify_url,
        "amount": {"total": payment.amount_cents, "currency": payment.currency},
        "payer": {"openid": openid},
    }
    response = _wechat_request("POST", "/v3/pay/transactions/jsapi", payload)
    prepay_id = response.get("prepay_id")
    if not prepay_id:
        raise WechatPayRequestError("微信支付未返回 prepay_id。")
    return prepay_id, response, sign_jsapi_pay_params(prepay_id)


def close_jsapi_payment(payment):
    if not payment.out_trade_no:
        return {}
    path = f"/v3/pay/transactions/out-trade-no/{payment.out_trade_no}/close"
    payload = {"mchid": settings.WECHAT_PAY_MCH_ID}
    return _wechat_request("POST", path, payload)


def request_refund(refund):
    notify_url = settings.WECHAT_PAY_REFUND_NOTIFY_URL or settings.WECHAT_PAY_NOTIFY_URL
    if not notify_url:
        raise WechatPayConfigError("WECHAT_PAY_REFUND_NOTIFY_URL 未配置。")
    payment = refund.payment
    payload = {
        "out_trade_no": payment.out_trade_no,
        "out_refund_no": refund.out_refund_no,
        "reason": refund.reason[:80] or "用户申请退款",
        "notify_url": notify_url,
        "amount": {
            "refund": refund.amount_cents,
            "total": refund.total_amount_cents,
            "currency": refund.currency,
        },
    }
    if payment.transaction_id:
        payload["transaction_id"] = payment.transaction_id
        payload.pop("out_trade_no", None)
    return payload, _wechat_request("POST", "/v3/refund/domestic/refunds", payload)


def decrypt_resource(resource):
    _x509, _invalid, _hashes, _serialization, _padding, AESGCM = _require_crypto()
    _ensure_base_config()
    try:
        ciphertext = base64.b64decode(resource["ciphertext"])
        nonce = resource["nonce"].encode("utf-8")
        associated_data = (resource.get("associated_data") or "").encode("utf-8")
        aesgcm = AESGCM(settings.WECHAT_PAY_APIV3_KEY.encode("utf-8"))
        plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data)
        return json.loads(plaintext.decode("utf-8"))
    except (KeyError, ValueError, TypeError) as exc:
        raise WechatPayRequestError("微信支付回调资源解密失败。") from exc


def verify_callback_signature(headers, raw_body: bytes):
    if not settings.WECHAT_PAY_VERIFY_CALLBACK_SIGNATURE:
        return True
    _x509, InvalidSignature, hashes, serialization, padding, _aes = _require_crypto()
    timestamp = headers.get("Wechatpay-Timestamp") or headers.get(
        "HTTP_WECHATPAY_TIMESTAMP"
    )
    nonce = headers.get("Wechatpay-Nonce") or headers.get("HTTP_WECHATPAY_NONCE")
    signature = headers.get("Wechatpay-Signature") or headers.get(
        "HTTP_WECHATPAY_SIGNATURE"
    )
    if not timestamp or not nonce or not signature:
        raise WechatPayRequestError("微信支付回调缺少验签头。")
    key_data = _platform_public_key_pem()
    if b"BEGIN CERTIFICATE" in key_data:
        cert = _x509.load_pem_x509_certificate(key_data)
        public_key = cert.public_key()
    else:
        public_key = serialization.load_pem_public_key(key_data)
    message = timestamp.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n"
    message += raw_body + b"\n"
    try:
        public_key.verify(
            base64.b64decode(signature),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError) as exc:
        raise WechatPayRequestError("微信支付回调验签失败。") from exc
    return True
