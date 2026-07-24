import base64
import json
import secrets
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request

from django.conf import settings


class WechatPayConfigError(Exception):
    pass


class WechatPayRequestError(Exception):
    def __init__(self, message, *, http_status=None, code="", response=None):
        super().__init__(message)
        self.http_status = http_status
        self.code = code or ""
        self.response = response or {}

    @property
    def is_network_error(self):
        return self.http_status is None


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


def _key_material_bytes(value) -> bytes:
    if not value:
        raise WechatPayConfigError("微信支付平台公钥内容为空。")
    normalized = str(value).replace("\\n", "\n")
    if "-----BEGIN " in normalized:
        return normalized.encode("utf-8")
    path = Path(normalized)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise WechatPayConfigError(f"无法读取微信支付公钥文件：{path}。") from exc


def _platform_public_key_pem(serial: str) -> bytes:
    key_map = getattr(settings, "WECHAT_PAY_PLATFORM_KEYS", {}) or {}
    if serial and serial in key_map:
        return _key_material_bytes(key_map[serial])
    if key_map:
        raise WechatPayRequestError(f"未知的微信支付平台公钥序列号：{serial or '空'}。")
    if getattr(settings, "IS_PRODUCTION", False):
        raise WechatPayConfigError("生产环境必须配置 WECHAT_PAY_PLATFORM_KEYS。")
    if settings.WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH:
        return _key_material_bytes(settings.WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH)
    if settings.WECHAT_PAY_PLATFORM_PUBLIC_KEY:
        return _key_material_bytes(settings.WECHAT_PAY_PLATFORM_PUBLIC_KEY)
    raise WechatPayConfigError("微信支付平台公钥/证书未配置，无法验签。")


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


def _load_platform_public_key(serial: str):
    x509, _invalid, _hashes, serialization, _padding, _aes = _require_crypto()
    key_data = _platform_public_key_pem(serial)
    if b"BEGIN CERTIFICATE" in key_data:
        return x509.load_pem_x509_certificate(key_data).public_key()
    return serialization.load_pem_public_key(key_data)


def _verify_wechat_signature(timestamp, nonce, signature, serial, raw_body: bytes):
    _x509, InvalidSignature, hashes, _serialization, padding, _aes = _require_crypto()
    if not timestamp or not nonce or not signature:
        raise WechatPayRequestError("微信支付响应缺少验签头。")
    message = timestamp.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n"
    message += raw_body + b"\n"
    try:
        _load_platform_public_key(serial).verify(
            base64.b64decode(signature),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError) as exc:
        raise WechatPayRequestError("微信支付响应验签失败。") from exc


def _response_header(headers, name):
    return headers.get(name) or headers.get(name.lower()) or headers.get(name.title())


def verify_wechat_response(headers, raw_body: bytes):
    if not settings.WECHAT_PAY_VERIFY_CALLBACK_SIGNATURE:
        return True
    _verify_wechat_signature(
        _response_header(headers, "Wechatpay-Timestamp"),
        _response_header(headers, "Wechatpay-Nonce"),
        _response_header(headers, "Wechatpay-Signature"),
        _response_header(headers, "Wechatpay-Serial"),
        raw_body,
    )
    return True


def _decoded_json(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"raw": raw.decode("utf-8", errors="replace")}


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
            raw = response.read()
            verify_wechat_response(response.headers, raw)
            return _decoded_json(raw)
    except url_error.HTTPError as exc:
        raw = exc.read()
        verify_wechat_response(exc.headers, raw)
        response = _decoded_json(raw)
        code = response.get("code", "") if isinstance(response, dict) else ""
        message = response.get("message", "") if isinstance(response, dict) else ""
        raise WechatPayRequestError(
            f"微信支付请求失败：HTTP {exc.code} {code} {message}".strip(),
            http_status=exc.code,
            code=code,
            response=response,
        ) from exc
    except (OSError, ValueError, url_error.URLError) as exc:
        raise WechatPayRequestError(
            "微信支付服务请求失败，请稍后重试。",
            response={"error": str(exc)},
        ) from exc


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


def build_jsapi_prepay_payload(payment, openid: str, description: str):
    notify_url = settings.WECHAT_PAY_NOTIFY_URL
    if not notify_url:
        raise WechatPayConfigError("WECHAT_PAY_NOTIFY_URL 未配置。")
    return {
        "appid": settings.WECHAT_MINI_APPID,
        "mchid": settings.WECHAT_PAY_MCH_ID,
        "description": description[:127] or "销售小程序订单",
        "out_trade_no": payment.out_trade_no,
        "notify_url": notify_url,
        "amount": {"total": payment.amount_cents, "currency": payment.currency},
        "payer": {"openid": openid},
    }


def create_jsapi_prepay(payment, openid: str, description: str, *, payload=None):
    payload = payload or build_jsapi_prepay_payload(payment, openid, description)
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


def query_jsapi_payment(payment):
    if not payment.out_trade_no:
        raise WechatPayRequestError("支付单缺少微信商户订单号。")
    out_trade_no = url_parse.quote(payment.out_trade_no, safe="")
    mchid = url_parse.quote(settings.WECHAT_PAY_MCH_ID, safe="")
    path = f"/v3/pay/transactions/out-trade-no/{out_trade_no}?mchid={mchid}"
    return _wechat_request("GET", path)


def build_refund_request_payload(refund):
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
    return payload


def request_refund(refund, *, payload=None):
    payload = payload or build_refund_request_payload(refund)
    return payload, _wechat_request("POST", "/v3/refund/domestic/refunds", payload)


def query_refund(refund):
    if not refund.out_refund_no:
        raise WechatPayRequestError("退款单缺少商户退款单号。")
    out_refund_no = url_parse.quote(refund.out_refund_no, safe="")
    path = f"/v3/refund/domestic/refunds/{out_refund_no}"
    return _wechat_request("GET", path)


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
    timestamp = headers.get("Wechatpay-Timestamp") or headers.get(
        "HTTP_WECHATPAY_TIMESTAMP"
    )
    nonce = headers.get("Wechatpay-Nonce") or headers.get("HTTP_WECHATPAY_NONCE")
    signature = headers.get("Wechatpay-Signature") or headers.get(
        "HTTP_WECHATPAY_SIGNATURE"
    )
    serial = headers.get("Wechatpay-Serial") or headers.get(
        "HTTP_WECHATPAY_SERIAL"
    )
    if not timestamp or not nonce or not signature:
        raise WechatPayRequestError("微信支付回调缺少验签头。")
    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise WechatPayRequestError("微信支付回调时间戳无效。") from exc
    max_age = max(
        int(getattr(settings, "WECHAT_PAY_CALLBACK_MAX_AGE_SECONDS", 300)),
        1,
    )
    if abs(int(time.time()) - timestamp_value) > max_age:
        raise WechatPayRequestError("微信支付回调时间戳已过期。")
    _verify_wechat_signature(timestamp, nonce, signature, serial, raw_body)
    return True
