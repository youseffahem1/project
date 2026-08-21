"""
كل تكامل Binance (إيداع عبر Binance Pay + سحب عبر Binance Withdraw API) محصور هنا فقط.
لا مكان ثاني بالكود يتكلم مباشرة مع Binance — هذا يخليك تقدر تبدل المزود لاحقاً
(مثلاً NowPayments أو مزود P2P محلي) بتعديل هذا الملف فقط.

مراجع رسمية (تحقق منها دايم لأن APIs تتحدث):
- Binance Pay: https://developers.binance.com/docs/binance-pay/introduction
- Binance Withdraw: https://developers.binance.com/docs/wallet/capital/withdraw
"""
import hmac
import hashlib
import json
import time
import uuid
import httpx

from .config import (
    BINANCE_PAY_API_KEY, BINANCE_PAY_API_SECRET, BINANCE_PAY_BASE_URL,
    BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_BASE_URL,
    WITHDRAW_ASSET, WITHDRAW_NETWORK, WEBHOOK_URL,
)


# =========================================================================
# 1) BINANCE PAY — إنشاء طلب إيداع (Checkout Order)
# =========================================================================

def _pay_signature(timestamp: str, nonce: str, body: str) -> str:
    """
    توقيع Binance Pay: HMAC-SHA512 لـ (timestamp + "\n" + nonce + "\n" + body + "\n")
    بالحروف الكبيرة (uppercase hex).
    """
    payload = f"{timestamp}\n{nonce}\n{body}\n"
    signature = hmac.new(
        BINANCE_PAY_API_SECRET.encode(), payload.encode(), hashlib.sha512
    ).hexdigest().upper()
    return signature


def create_binance_pay_order(user_id: str, amount_usdt: float, merchant_trade_no: str) -> dict:
    """
    ينشئ طلب دفع في Binance Pay ويرجع رابط/QR يفتحه المستخدم بتطبيق Binance
    ليدفع منه USDT مباشرة. merchant_trade_no لازم يكون فريد لكل عملية —
    نستخدم نفس id الخاص بسجل Deposit عندنا في قاعدة البيانات.
    """
    timestamp = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex[:32]

    body_dict = {
        "env": {"terminalType": "WEB"},
        "merchantTradeNo": merchant_trade_no,
        "orderAmount": amount_usdt,
        "currency": "USDT",
        "description": f"LuckySpin deposit - user {user_id}",
        "goodsDetails": [{
            "goodsType": "02",
            "goodsCategory": "Z000",
            "referenceGoodsId": "points_topup",
            "goodsName": "LuckySpin Points Top-up",
        }],
        "webhookUrl": WEBHOOK_URL,
    }
    body = json.dumps(body_dict, separators=(",", ":"))
    signature = _pay_signature(timestamp, nonce, body)

    headers = {
        "Content-Type": "application/json",
        "BinancePay-Timestamp": timestamp,
        "BinancePay-Nonce": nonce,
        "BinancePay-Certificate-SN": BINANCE_PAY_API_KEY,
        "BinancePay-Signature": signature,
    }

    resp = httpx.post(
        f"{BINANCE_PAY_BASE_URL}/binancepay/openapi/v3/order",
        headers=headers, content=body, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "SUCCESS":
        raise RuntimeError(f"Binance Pay error: {data}")

    result = data["data"]
    return {
        "prepay_id": result["prepayId"],
        "checkout_url": result["checkoutUrl"],
        "qr_code": result.get("qrcodeLink"),
        "deeplink": result.get("deeplink"),
        "expire_time": result.get("expireTime"),
    }


def verify_pay_webhook(timestamp: str, nonce: str, body: str, received_signature: str) -> bool:
    """
    يتحقق أن إشعار الدفع (webhook) فعلاً جاي من Binance وما هو مزوّر.
    *** بدون هذا التحقق أي حد يقدر يرسل طلب وهمي ويفعّل نقاط مجانية ***
    """
    expected = _pay_signature(timestamp, nonce, body)
    return hmac.compare_digest(expected, received_signature)


# =========================================================================
# 2) BINANCE WITHDRAW — سحب USDT فعلي لعنوان المستخدم
# =========================================================================

def _withdraw_signature(query_string: str) -> str:
    """توقيع Binance الكلاسيكي: HMAC-SHA256 على query string، hex lowercase"""
    return hmac.new(
        BINANCE_API_SECRET.encode(), query_string.encode(), hashlib.sha256
    ).hexdigest()


def submit_binance_withdrawal(address: str, amount_usdt: float, client_order_id: str) -> dict:
    """
    يرسل طلب سحب USDT فعلي من محفظة المنصة إلى عنوان المستخدم.
    *** استخدم مفتاح API بصلاحية Withdraw فقط، وفعّل IP whitelist على Binance ***
    *** لا تستدعي هذي الدالة مباشرة من endpoint عام بدون التحقق من صلاحية وهوية المستخدم أولاً ***
    """
    timestamp = int(time.time() * 1000)
    params = {
        "coin": "USDT",
        "network": WITHDRAW_NETWORK,
        "address": address,
        "amount": amount_usdt,
        "withdrawOrderId": client_order_id,   # معرف فريد عندنا لمنع تكرار السحب (idempotency)
        "timestamp": timestamp,
        "recvWindow": 5000,
    }
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    signature = _withdraw_signature(query_string)

    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    resp = httpx.post(
        f"{BINANCE_BASE_URL}/sapi/v1/capital/withdraw/apply",
        params={**params, "signature": signature},
        headers=headers, timeout=15,
    )
    resp.raise_for_status()
    return resp.json()  # يحتوي على "id" = withdrawal id من Binance


def get_withdrawal_status(withdraw_id: str) -> dict:
    """يستعلم عن حالة سحب سابق (Email Sent / Cancelled / Awaiting Approval / Rejected / Processing / Completed)"""
    timestamp = int(time.time() * 1000)
    params = {"withdrawOrderId": withdraw_id, "timestamp": timestamp, "recvWindow": 5000}
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    signature = _withdraw_signature(query_string)

    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    resp = httpx.get(
        f"{BINANCE_BASE_URL}/sapi/v1/capital/withdraw/history",
        params={**params, "signature": signature},
        headers=headers, timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
