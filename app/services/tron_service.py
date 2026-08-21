"""
طبقة اتصال واحدة مع شبكة TRON (Nile Testnet فقط بهذه المرحلة).

مسؤولياته:
- قراءة معاملات TRX الواردة لعنوان معيّن (لمراقبة الإيداعات)
- جلب تفاصيل معاملة واحدة بالـ hash (للتحقق ولإندبوينت /transactions/{tx_hash})
- التحقق من صحة شكل عنوان TRON
- بناء وتوقيع وبث معاملة سحب TRX فعلية على Nile عبر محفظة السحب المخصصة
  (TRON_WITHDRAWAL_PRIVATE_KEY) — محفظة منفصلة تماماً عن عناوين إيداع المستخدمين

*** لا يوجد هنا أي مفتاح خاص لأي مستخدم — فقط مفتاح محفظة السحب الواحدة، من env فقط ***
"""
import hashlib
import base58
import httpx

from ..config import TRON_API_URL, TRON_WITHDRAWAL_PRIVATE_KEY, SUN_PER_TRX, TRONGRID_API_KEY


class TronServiceError(Exception):
    pass


def _tron_headers() -> dict:
    """TronGrid API key header — optional on Nile, effectively required for
    reasonable Mainnet rate limits. Omitted entirely when not configured."""
    return {"TRON-PRO-API-KEY": TRONGRID_API_KEY} if TRONGRID_API_KEY else {}


# =========================================================================
# تحويل عنوان TRON من hex (41...) إلى base58 (T...)
# =========================================================================

def hex_to_base58_address(hex_addr: str) -> str:
    if not hex_addr:
        return hex_addr
    if hex_addr.startswith("0x"):
        hex_addr = hex_addr[2:]
    try:
        addr_bytes = bytes.fromhex(hex_addr)
    except ValueError:
        return hex_addr  # مو hex أصلاً، رجّعه كما هو (غالباً base58 جاهز)
    checksum = hashlib.sha256(hashlib.sha256(addr_bytes).digest()).digest()[:4]
    return base58.b58encode(addr_bytes + checksum).decode()


def is_valid_tron_address(address: str) -> bool:
    """
    تحقق حقيقي من عنوان TRON بصيغة Base58Check.
    """
    if not isinstance(address, str):
        return False

    address = address.strip()

    if len(address) != 34 or not address.startswith("T"):
        return False

    try:
        from tronpy import Tron

        return Tron.is_base58check_address(address)

    except (ImportError, ValueError, TypeError):
        return False


# =========================================================================
# قراءة معاملات عنوان معيّن (لمراقبة الإيداعات)
# =========================================================================

def get_address_transactions(address: str, limit: int = 20) -> list:
    """
    يرجع آخر معاملات (native TRX transfers) واردة/صادرة لعنوان معيّن عبر
    TronGrid REST API على Nile. كل عنصر يرجع بشكل مُطبّع (normalized):
    {tx_hash, sender, receiver, amount_trx, block_number, timestamp, success, confirmations}
    """
    url = f"{TRON_API_URL}/v1/accounts/{address}/transactions"
    params = {"limit": limit, "only_confirmed": "true"}
    try:
        resp = httpx.get(url, params=params, headers=_tron_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise TronServiceError(f"TRON network error: {e}") from e

    latest_block = get_latest_block_number()
    results = []

    for tx in data.get("data", []):
        try:
            contract = tx["raw_data"]["contract"][0]
            if contract.get("type") != "TransferContract":
                continue  # نهتم فقط بتحويلات TRX العادية، مو smart contracts

            value = contract["parameter"]["value"]
            sender = hex_to_base58_address(value.get("owner_address", ""))
            receiver = hex_to_base58_address(value.get("to_address", ""))
            amount_sun = value.get("amount", 0)

            ret = tx.get("ret", [{}])
            success = bool(ret) and ret[0].get("contractRet") == "SUCCESS"
            block_number = tx.get("blockNumber")
            confirmations = (latest_block - block_number) if (latest_block and block_number) else 0

            results.append({
                "tx_hash": tx.get("txID"),
                "sender": sender,
                "receiver": receiver,
                "amount_trx": amount_sun / SUN_PER_TRX,
                "block_number": block_number,
                "timestamp": tx.get("block_timestamp"),
                "success": success,
                "confirmations": max(confirmations, 0),
            })
        except (KeyError, IndexError, TypeError):
            continue  # معاملة بشكل غير متوقع (smart contract معقد مثلاً) — تجاهلها بأمان

    return results


def get_latest_block_number() -> int:
    try:
        resp = httpx.post(f"{TRON_API_URL}/wallet/getnowblock", headers=_tron_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json().get("block_header", {}).get("raw_data", {}).get("number", 0)
    except Exception:
        return 0


def get_transaction_info(tx_hash: str) -> dict:
    """
    يجلب معلومات معاملة TRON Nile ويتحقق من نجاحها بشكل صحيح.

    نستخدم:
    - gettransactioninfobyid للحصول على blockNumber والرسوم.
    - gettransactionbyid للحصول على contractRet الحقيقي.
    """
    try:
        # معلومات التنفيذ والرسوم
        info_resp = httpx.post(
            f"{TRON_API_URL}/wallet/gettransactioninfobyid",
            json={"value": tx_hash},
            headers=_tron_headers(),
            timeout=15,
        )
        info_resp.raise_for_status()
        info = info_resp.json()

        if not info or "id" not in info:
            raise TronServiceError("Transaction not found on TRON Nile")

        # تفاصيل المعاملة نفسها لمعرفة contractRet
        tx_resp = httpx.post(
            f"{TRON_API_URL}/wallet/gettransactionbyid",
            json={"value": tx_hash},
            headers=_tron_headers(),
            timeout=15,
        )
        tx_resp.raise_for_status()
        tx = tx_resp.json()

    except httpx.HTTPError as e:
        raise TronServiceError(f"TRON network error: {e}") from e

    if not tx:
        raise TronServiceError("Transaction details not found on TRON Nile")

    # TRON يضع نتيجة العقد هنا:
    # tx["ret"][0]["contractRet"]
    contract_ret = None
    ret = tx.get("ret")

    if isinstance(ret, list) and ret:
        contract_ret = ret[0].get("contractRet")

    # إذا كانت المعاملة ما زالت غير محسومة
    # لا نعتبرها ناجحة لمجرد وجود المعاملة.
    if contract_ret is None:
        success = False
    else:
        success = contract_ret == "SUCCESS"

    latest_block = get_latest_block_number()
    block_number = info.get("blockNumber", 0)

    confirmations = 0
    if block_number and latest_block:
        confirmations = max(latest_block - block_number, 0)

    return {
        "tx_hash": info.get("id") or tx_hash,
        "block_number": block_number,
        "confirmations": confirmations,
        "success": success,
        "fee": info.get("fee", 0),
    }


# =========================================================================
# إرسال سحب TRX فعلي — محفظة سحب مخصصة، منفصلة عن عناوين المستخدمين
# =========================================================================

def send_trx_withdrawal(to_address: str, amount_sun: int, sequence_id: str) -> dict:
    """
    يبني ويوقّع ويبث معاملة سحب TRX على TRON Nile.

    محفظة السحب منفصلة عن عناوين الإيداع.
    """

    if not TRON_WITHDRAWAL_PRIVATE_KEY:
        raise TronServiceError(
            "TRON_WITHDRAWAL_PRIVATE_KEY is not configured"
        )

    to_address = to_address.strip()

    # تحقق حقيقي من عنوان TRON قبل أي محاولة إرسال
    if not is_valid_tron_address(to_address):
        raise TronServiceError("Invalid TRON destination address")

    try:
        from tronpy import Tron
        from tronpy.keys import PrivateKey
        from tronpy.providers import HTTPProvider
    except ImportError as e:
        raise TronServiceError(
            f"tronpy not installed: {e}"
        ) from e

    try:
        client = Tron(HTTPProvider(TRON_API_URL))

        priv_key = PrivateKey(
            bytes.fromhex(TRON_WITHDRAWAL_PRIVATE_KEY)
        )

        from_address = priv_key.public_key.to_base58check_address()

        # منع السحب إلى محفظة السحب نفسها
        if from_address == to_address:
            raise TronServiceError(
                "Withdrawal destination cannot be the withdrawal wallet itself"
            )

        txn = (
            client.trx.transfer(
                from_address,
                to_address,
                amount_sun,
            )
            .memo(f"luckyspin-withdraw:{sequence_id}")
            .build()
            .sign(priv_key)
        )

        result = txn.broadcast()

    except TronServiceError:
        raise

    except Exception as e:
        raise TronServiceError(
            f"Failed to send TRON withdrawal: {e}"
        ) from e

    tx_hash = result.get("txid") or getattr(txn, "txid", None)

    if not tx_hash:
        raise TronServiceError(
            f"TRON broadcast did not return transaction hash: {result}"
        )

    return {
        "tx_hash": tx_hash
    }


# =========================================================================
# NEW: USDT-TRC20 (a TRC20 token transfer on the SAME TRON address as TRX
# above — no separate address needed). Fully additive; every function above
# this line is completely untouched.
# =========================================================================

def get_trc20_transfers(address: str, contract_address: str, limit: int = 20) -> list:
    """
    Discovers candidate incoming TRC20 token transfers (e.g. USDT) to
    `address` via TronGrid's dedicated trc20 transfer-list endpoint. This is
    a DISCOVERY step only — each candidate is re-verified for real
    confirmations/success via get_transaction_info() before ever being
    credited (same verification path already used for native TRX), so we
    never trust this endpoint's shape/fields as final truth by itself.
    """
    url = f"{TRON_API_URL}/v1/accounts/{address}/transactions/trc20"
    params = {"limit": limit, "contract_address": contract_address, "only_to": "true"}
    try:
        resp = httpx.get(url, params=params, headers=_tron_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise TronServiceError(f"TRON network error: {e}") from e

    results = []
    for tx in data.get("data", []):
        try:
            if tx.get("to") != address:
                continue
            token_info = tx.get("token_info", {}) or {}
            if token_info.get("address") != contract_address:
                continue  # لازم يكون بالضبط نفس عقد USDT — أي توكن ثاني يُتجاهل
            decimals = int(token_info.get("decimals", 6))
            raw_value = int(tx.get("value", 0))
            amount = raw_value / (10 ** decimals)
            if amount <= 0:
                continue

            results.append({
                "tx_hash": tx.get("transaction_id"),
                "sender": tx.get("from"),
                "receiver": tx.get("to"),
                "amount_token": amount,
                "timestamp": tx.get("block_timestamp"),
            })
        except (KeyError, TypeError, ValueError):
            continue  # شكل غير متوقع — تجاهله بأمان بدل ما يكسر الفحص كله

    return results


def send_trc20_withdrawal(to_address: str, amount_token: float, contract_address: str,
                           decimals: int, sequence_id: str) -> dict:
    """
    Builds, signs, and broadcasts a real TRC20 token transfer (e.g. USDT) on
    TRON using the dedicated withdrawal wallet — same wallet as
    send_trx_withdrawal(), separate from any user's deposit address.
    """
    if not TRON_WITHDRAWAL_PRIVATE_KEY:
        raise TronServiceError("TRON_WITHDRAWAL_PRIVATE_KEY is not configured")

    to_address = to_address.strip()
    if not is_valid_tron_address(to_address):
        raise TronServiceError("Invalid TRON destination address")

    try:
        from tronpy import Tron
        from tronpy.keys import PrivateKey
        from tronpy.providers import HTTPProvider
    except ImportError as e:
        raise TronServiceError(f"tronpy not installed: {e}") from e

    try:
        client = Tron(HTTPProvider(TRON_API_URL))
        priv_key = PrivateKey(bytes.fromhex(TRON_WITHDRAWAL_PRIVATE_KEY))
        from_address = priv_key.public_key.to_base58check_address()

        if from_address == to_address:
            raise TronServiceError("Withdrawal destination cannot be the withdrawal wallet itself")

        amount_base_units = int(round(amount_token * (10 ** decimals)))
        contract = client.get_contract(contract_address)

        txn = (
            contract.functions.transfer(to_address, amount_base_units)
            .with_owner(from_address)
            .fee_limit(30_000_000)  # حد أقصى للرسوم بـ sun — يحمي من رسوم غير متوقعة
            .build()
            .sign(priv_key)
        )
        result = txn.broadcast()
    except TronServiceError:
        raise
    except Exception as e:
        raise TronServiceError(f"Failed to send USDT-TRC20 withdrawal: {e}") from e

    tx_hash = result.get("txid") or getattr(txn, "txid", None)
    if not tx_hash:
        raise TronServiceError(f"TRON broadcast did not return transaction hash: {result}")

    return {"tx_hash": tx_hash}
