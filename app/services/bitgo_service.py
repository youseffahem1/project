"""
طبقة اتصال واحدة مع BitGo API. BitGo يدير كل المفاتيح الخاصة من جهته —
هذا المشروع أبداً ما يخزن ولا يشوف seed أو private key، فقط access token
بصلاحيات محدودة عبر متغيرات البيئة.

مسؤوليات هذا الملف فقط (ولا شي غيرها):
- الاتصال بـ BitGo API
- جلب بيانات المحفظة
- إنشاء Receive Address
- جلب Transactions / Transfers
- تنفيذ Withdrawal

مرجع رسمي (تحقق دايماً، الـ API ممكن يتحدث):
https://developers.bitgo.com/api/v2/express/wallet
"""
import hashlib
import httpx
import base58

from ..config import (
    BITGO_ACCESS_TOKEN, BITGO_WALLET_ID, BITGO_COIN, BITGO_ENV,
    BITGO_WALLET_PASSPHRASE,
)

_BASE_URLS = {
    "test": "https://app.bitgo-test.com",
    "prod": "https://app.bitgo.com",
}


class BitGoError(Exception):
    """خطأ عام من BitGo API — الرسالة نظيفة، بدون أي تفاصيل حساسة (توكن، إلخ)"""
    pass


def _base_url() -> str:
    return _BASE_URLS.get(BITGO_ENV, _BASE_URLS["test"])


def _headers() -> dict:
    if not BITGO_ACCESS_TOKEN:
        raise BitGoError("BITGO_ACCESS_TOKEN is not configured")
    # *** لا تطبع أو تسجل هذا الهيدر بأي log أبداً ***
    return {
        "Authorization": f"Bearer {BITGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, params: dict = None, json_body: dict = None) -> dict:
    url = f"{_base_url()}/api/v2/{BITGO_COIN}{path}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(method, url, headers=_headers(), params=params, json=json_body)
    except httpx.RequestError as e:
        raise BitGoError(f"BitGo network error: {e}") from e

    if resp.status_code >= 400:
        # نلتقط رسالة الخطأ من BitGo بدون تسريب هيدرات الطلب (فيها التوكن)
        try:
            detail = resp.json().get("error", resp.text)
        except Exception:
            detail = resp.text
        raise BitGoError(f"BitGo API error ({resp.status_code}): {detail}")

    return resp.json()


# =========================================================================
# NEW: real BTC address format validation (mainnet + testnet — BITGO_ENV
# defaults to "test"/tbtc, so testnet formats matter here). This is a real,
# from-scratch check (base58check + bech32 shape), the same spirit as
# tron_service.is_valid_tron_address() — not a rubber stamp. It is not the
# ONLY safety net: BitGo's own API will still reject a truly invalid address
# when send_withdrawal() is called, exactly like TRON does for TRX/USDT.
# =========================================================================

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
# version byte -> real network+type (0x00 BTC P2PKH, 0x05 BTC P2SH,
# 0x6f testnet P2PKH, 0xc4 testnet P2SH)
_BASE58_VERSION_BYTES = {0x00, 0x05, 0x6F, 0xC4}


def is_valid_btc_address(address: str) -> bool:
    if not isinstance(address, str):
        return False
    address = address.strip()
    if not address:
        return False

    lowered = address.lower()
    if lowered.startswith("bc1") or lowered.startswith("tb1"):
        # Bech32 / Bech32m SegWit address (mainnet bc1..., testnet tb1...)
        data_part = lowered[3:]
        if not (11 <= len(data_part) <= 71):
            return False
        return all(c in _BECH32_CHARSET for c in data_part)

    # Legacy Base58Check (P2PKH "1...", P2SH "3...", or testnet "m/n/2...")
    try:
        decoded = base58.b58decode(address)
    except Exception:
        return False
    if len(decoded) != 25:
        return False
    version = decoded[0]
    payload, checksum = decoded[1:21], decoded[21:]
    calc_checksum = hashlib.sha256(hashlib.sha256(decoded[:21]).digest()).digest()[:4]
    if checksum != calc_checksum:
        return False
    return version in _BASE58_VERSION_BYTES


# =========================================================================
# 1) بيانات المحفظة
# =========================================================================

def get_wallet() -> dict:
    """يرجع بيانات محفظة BitGo (الرصيد، العملة، إلخ)"""
    if not BITGO_WALLET_ID:
        raise BitGoError("BITGO_WALLET_ID is not configured")
    return _request("GET", f"/wallet/{BITGO_WALLET_ID}")


# =========================================================================
# 2) إنشاء عنوان استلام جديد
# =========================================================================

def create_receive_address(label: str = None) -> dict:
    """
    يطلب من BitGo عنوان إيداع جديد على نفس المحفظة. كل عنوان فريد،
    وBitGo هو اللي يتابعه على البلوكتشين من جهته.
    يرجع: {"address": "...", "id": "...", ...}
    """
    body = {}
    if label:
        body["label"] = label
    return _request("POST", f"/wallet/{BITGO_WALLET_ID}/address", json_body=body)


# =========================================================================
# 3) جلب المعاملات (Transfers)
# =========================================================================

def list_wallet_transfers(limit: int = 50, address: str = None, state: str = None) -> dict:
    """
    يرجع آخر معاملات المحفظة (واردة وصادرة). نقدر نصفيها بعنوان معين أو حالة معينة
    (state مثل "confirmed").
    """
    params = {"limit": limit}
    if address:
        params["address"] = address
    if state:
        params["state"] = state
    return _request("GET", f"/wallet/{BITGO_WALLET_ID}/transfer", params=params)


def get_transfer(transfer_id: str) -> dict:
    """يرجع تفاصيل معاملة واحدة بالضبط — نستخدمه لمتابعة حالة سحب معين"""
    return _request("GET", f"/wallet/{BITGO_WALLET_ID}/transfer/{transfer_id}")


# =========================================================================
# 4) تنفيذ سحب (Withdrawal)
# =========================================================================

def send_withdrawal(address: str, amount_base_units: str, sequence_id: str) -> dict:
    """
    يرسل طلب سحب فعلي عبر BitGo.

    amount_base_units: المبلغ بأصغر وحدة للعملة (مثال: sun بالنسبة لـ TRX)
        كنص (string) — BitGo يتوقعها كذا لتفادي مشاكل دقة الأرقام العشرية.
    sequence_id: معرف فريد لعملية السحب هذي بالذات (نستخدم Withdrawal.id عندنا).
        BitGo يستخدمه كـ idempotency key رسمي: لو انبعث نفس sequenceId مرتين
        بالغلط (retry شبكي مثلاً)، BitGo ما ينفذ السحب مرتين.

    *** لا تستدعي هذي الدالة مباشرة من أي مكان بدون التحقق من هوية
    وصلاحية المستخدم ورصيده أولاً في طبقة الـ route ***
    """
    body = {
        "address": address,
        "amount": amount_base_units,
        "sequenceId": sequence_id,
    }
    # بعض أنواع المحافظ (self-managed hot wallet) تحتاج passphrase لتوقيع المعاملة.
    # محافظ custodial/institutional ما تحتاجها. نمررها بس لو متوفرة بالإعدادات.
    if BITGO_WALLET_PASSPHRASE:
        body["walletPassphrase"] = BITGO_WALLET_PASSPHRASE

    return _request("POST", f"/wallet/{BITGO_WALLET_ID}/sendcoins", json_body=body)
