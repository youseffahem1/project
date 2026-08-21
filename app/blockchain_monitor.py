"""
يفحص دورياً كل عناوين إيداع المستخدمين (TRON + BitGo BTC)، ويحسب النقاط
تلقائياً بمجرد وصول التأكيدات الكافية ومرور فحص الحد الأدنى ($5 مكافئ) لأي
معاملة واردة جديدة — TRX، USDT-TRC20 (نفس عنوان TRON)، أو BTC عبر BitGo.

يشتغل كـ background task يبدأ مع تشغيل السيرفر (شوف main.py).

لا يثق بأي شي غير راجع من الشبكة نفسها: العنوان، المرسل، المبلغ، رقم البلوك،
والنجاح كلها تُستخرج من استجابة الشبكة مباشرة عبر tron_service/bitgo_service.
"""
from datetime import datetime
from decimal import Decimal

from .database import SessionLocal
from . import models
from . import email_service
from .services import tron_service, price_service
from .config import (
    TRON_MIN_CONFIRMATIONS, TRON_POLL_INTERVAL_SECONDS,
    TRON_ADDRESS_CURRENCY_LABEL, POINTS_PER_USDT,
    USDT_TRC20_CONTRACT, USDT_TRC20_DECIMALS, USDT_MIN_CONFIRMATIONS,
    BTC_ADDRESS_CURRENCY_LABEL, BTC_MIN_CONFIRMATIONS, BITGO_POLL_INTERVAL_SECONDS,
    MIN_DEPOSIT_USD,
)

# حارس بسيط يمنع تشغيل أكثر من حلقة مراقبة بنفس العملية بالغلط
_monitor_started = False


def _credit_deposit(db, user_id, currency, tx_hash, sender, receiver,
                     amount_crypto, block_number, confirmations, usd_value):
    """
    نقطة الاعتماد الوحيدة لأي إيداع بلوكتشين، لأي عملة (TRX / USDT_TRC20 /
    BTC). idempotent على tx_hash (فريد بكل الشبكات هنا). usd_value لازم
    يجي من سعر حقيقي (price_service) أو يساوي amount_crypto مباشرة لو
    العملة USDT (مربوطة 1:1 بالدولار أصلاً، ما تحتاج سعر خارجي).
    """
    already = db.query(models.BlockchainDeposit).filter_by(tx_hash=tx_hash).first()
    if already:
        return  # idempotency: هذا الـ tx_hash محسوب مسبقاً (أي حالة كانت)

    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        return

    # --- الحد الأدنى: $5 مكافئ (config.MIN_DEPOSIT_USD)، محسوب من سعر حقيقي،
    #     وليس رقم ثابت. لو أقل من الحد: نسجل المعاملة (تمنع إعادة المعالجة
    #     ونحفظ الأثر) لكن status="BELOW_MINIMUM" و credited=False — بدون
    #     أي إضافة رصيد، ونوضح السبب للمستخدم بإشعار. ---
    if usd_value < MIN_DEPOSIT_USD:
        db.add(models.BlockchainDeposit(
            user_id=user_id, currency=currency, tx_hash=tx_hash,
            sender=sender, receiver=receiver, block_number=block_number,
            amount_crypto=amount_crypto, amount_usdt_equivalent=usd_value,
            points_credited=0, confirmations=confirmations,
            status="BELOW_MINIMUM", credited=False,
        ))
        db.add(models.Notification(
            user_id=user_id, type="DEPOSIT_BELOW_MINIMUM", title="Deposit below minimum",
            message=(
                f"We received {amount_crypto} {currency} (≈ ${usd_value:.2f}), but the minimum "
                f"deposit is ${MIN_DEPOSIT_USD:.2f}. This amount was not credited to your balance."
            ),
        ))
        db.commit()
        return

    points = float(Decimal(str(usd_value)) * Decimal(str(POINTS_PER_USDT)))

    db.add(models.BlockchainDeposit(
        user_id=user_id, currency=currency, tx_hash=tx_hash,
        sender=sender, receiver=receiver, block_number=block_number,
        amount_crypto=amount_crypto, amount_usdt_equivalent=usd_value,
        points_credited=points, confirmations=confirmations,
        status="CONFIRMED", credited=True,
    ))

    user.points_balance = float(Decimal(str(user.points_balance)) + Decimal(str(points)))

    db.add(models.Transaction(
        user_id=user_id, type=models.TransactionType.DEPOSIT_UNLOCK,
        amount=points, description=f"Deposit {amount_crypto} {currency} confirmed (tx {tx_hash[:10]}..., block {block_number})",
    ))
    db.add(models.Notification(
        user_id=user_id, type="DEPOSIT_SUCCESS", title="Deposit successful",
        message=f"Amount: {amount_crypto} {currency}\nTransaction: {tx_hash}",
    ))

    db.commit()

    try:
        email_service.send_deposit_confirmed_email(
            user_name=user.full_name, user_email=user.email,
            currency=currency, amount=amount_crypto,
            deposit_address=receiver, sender_address=sender, tx_hash=tx_hash,
            block_number=block_number, confirmations=confirmations,
            confirmed_time=datetime.utcnow(),
        )
    except Exception as e:
        print("[blockchain_monitor] deposit email failed (deposit still credited): " + str(e))


def poll_all_wallets_once(db=None):
    """
    يفحص كل عناوين TRON المخزنة، ويحسب أي إيداع جديد مؤكد. دالة sync عادية —
    main.py يناديها من داخل حلقة async مع asyncio.sleep بينها.
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    try:
        wallets = db.query(models.UserWallet).filter_by(currency=TRON_ADDRESS_CURRENCY_LABEL).all()

        for wallet in wallets:
            try:
                txs = tron_service.get_address_transactions(wallet.address, limit=20)
            except tron_service.TronServiceError as e:
                print(f"[blockchain_monitor] error checking {wallet.address}: {e}")
                continue

            for tx in txs:
                if not tx.get("success"):
                    continue
                if tx.get("receiver") != wallet.address:
                    continue  # نهتم فقط بالمعاملات الواردة لعنوان هذا المستخدم بالضبط
                if tx.get("confirmations", 0) < TRON_MIN_CONFIRMATIONS:
                    continue
                if not tx.get("tx_hash") or tx.get("amount_trx", 0) <= 0:
                    continue

                # ROOT-CAUSE FIX: addresses are deterministic (seed + sequential
                # derivation_index), so if the local DB is ever reset during dev/
                # testing, a brand-new signup can be handed an index that was
                # already used before — and that address may already carry old
                # transactions on the real TRON chain. Without this check, those
                # pre-existing transactions would get auto-credited to whichever
                # new user now happens to own that recycled address, making it
                # look like a fresh signup already has a deposit. Only credit
                # transactions that happened at or after this wallet record was
                # actually created for this user.
                tx_timestamp_ms = tx.get("timestamp")
                if tx_timestamp_ms and wallet.created_at:
                    tx_time = datetime.utcfromtimestamp(tx_timestamp_ms / 1000)
                    if tx_time < wallet.created_at:
                        continue

                try:
                    trx_usd_price = price_service.get_usd_price("TRX")
                except price_service.PriceUnavailableError as e:
                    print(f"[blockchain_monitor] TRX price unavailable, skipping this pass: {e}")
                    break  # لا نخمن السعر — نجرب مرة ثانية بالدورة الجاية
                usd_value = tx["amount_trx"] * trx_usd_price

                _credit_deposit(
                    db, wallet.user_id, TRON_ADDRESS_CURRENCY_LABEL, tx["tx_hash"], tx["sender"], tx["receiver"],
                    tx["amount_trx"], tx["block_number"], tx["confirmations"], usd_value,
                )
    finally:
        if owns_session:
            db.close()


def poll_trc20_deposits_once(db=None):
    """
    يفحص نفس عناوين TRON المخزنة (TRX_NILE) بحثاً عن تحويلات USDT-TRC20 —
    ما يحتاج عنوان منفصل، لأن التوكن يعيش فوق نفس عنوان TRON. كل معاملة
    مرشحة تُتحقق فعلياً عبر get_transaction_info() (نفس مسار التحقق
    المستخدم لـ TRX أعلاه) قبل أي اعتماد — ما نثق بشكل رد trc20-list وحده.
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    try:
        wallets = db.query(models.UserWallet).filter_by(currency=TRON_ADDRESS_CURRENCY_LABEL).all()

        for wallet in wallets:
            try:
                candidates = tron_service.get_trc20_transfers(
                    wallet.address, USDT_TRC20_CONTRACT, limit=20,
                )
            except tron_service.TronServiceError as e:
                print(f"[blockchain_monitor] TRC20 check error {wallet.address}: {e}")
                continue

            for c in candidates:
                tx_hash = c.get("tx_hash")
                if not tx_hash:
                    continue
                if db.query(models.BlockchainDeposit.id).filter_by(tx_hash=tx_hash).first():
                    continue  # already processed (any status) — skip the extra verification call

                tx_timestamp_ms = c.get("timestamp")
                if tx_timestamp_ms and wallet.created_at:
                    tx_time = datetime.utcfromtimestamp(tx_timestamp_ms / 1000)
                    if tx_time < wallet.created_at:
                        continue  # same address-reuse-after-reset protection as TRX above

                try:
                    info = tron_service.get_transaction_info(tx_hash)
                except tron_service.TronServiceError as e:
                    print(f"[blockchain_monitor] could not verify TRC20 tx {tx_hash}: {e}")
                    continue

                if not info.get("success"):
                    continue
                if info.get("confirmations", 0) < USDT_MIN_CONFIRMATIONS:
                    continue

                # USDT is ~1:1 with USD — no external price lookup needed
                usd_value = c["amount_token"]

                _credit_deposit(
                    db, wallet.user_id, "USDT_TRC20", tx_hash, c.get("sender"), c.get("receiver"),
                    c["amount_token"], info.get("block_number"), info.get("confirmations", 0), usd_value,
                )
    finally:
        if owns_session:
            db.close()


def poll_bitgo_wallet_once(db=None):
    """
    يفحص محفظة BitGo الواحدة عن تحويلات BTC واردة مؤكدة، ويطابق كل تحويل
    بعنوان مستخدم معين (UserWallet بعملة BTC). *** غير مُختبر بعد على حساب
    BitGo حقيقي — الشكل بالضبط يعتمد على استجابة BitGo API الفعلية، راجع
    تقرير التسليم. ***
    """
    from .services import bitgo_service
    from .config import BITGO_ACCESS_TOKEN, BITGO_WALLET_ID

    if not BITGO_ACCESS_TOKEN or not BITGO_WALLET_ID:
        return  # BitGo غير مُهيأ بعد — لا نحاول شيء بدل ما نفشل بصمت بلوب

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    try:
        wallets = db.query(models.UserWallet).filter_by(currency=BTC_ADDRESS_CURRENCY_LABEL).all()
        if not wallets:
            return
        address_to_wallet = {w.address: w for w in wallets}

        try:
            resp = bitgo_service.list_wallet_transfers(limit=100, state="confirmed")
        except bitgo_service.BitGoError as e:
            print(f"[blockchain_monitor] BitGo error: {e}")
            return

        for transfer in resp.get("transfers", []):
            if transfer.get("type") != "receive":
                continue
            tx_hash = transfer.get("txid") or transfer.get("id")
            if not tx_hash:
                continue
            if db.query(models.BlockchainDeposit.id).filter_by(tx_hash=tx_hash).first():
                continue

            entries = transfer.get("entries", []) or []
            matched_wallet = None
            amount_satoshi = 0
            for entry in entries:
                w = address_to_wallet.get(entry.get("address"))
                if w and entry.get("value", 0) > 0:
                    matched_wallet = w
                    amount_satoshi = entry.get("value", 0)
                    break
            if not matched_wallet:
                continue

            confirmations = transfer.get("confirmations", 0)
            if confirmations < BTC_MIN_CONFIRMATIONS:
                continue

            # نفس حماية إعادة استخدام العنوان بعد reset، مطابقة للـ TRON أعلاه
            created_ts = transfer.get("date")
            if created_ts and matched_wallet.created_at:
                try:
                    tx_time = datetime.fromisoformat(created_ts.replace("Z", "+00:00")).replace(tzinfo=None)
                    if tx_time < matched_wallet.created_at:
                        continue
                except (ValueError, AttributeError):
                    pass

            amount_btc = amount_satoshi / 100_000_000
            try:
                btc_usd_price = price_service.get_usd_price("BTC")
            except price_service.PriceUnavailableError as e:
                print(f"[blockchain_monitor] BTC price unavailable, skipping this pass: {e}")
                break
            usd_value = amount_btc * btc_usd_price

            _credit_deposit(
                db, matched_wallet.user_id, BTC_ADDRESS_CURRENCY_LABEL, tx_hash,
                None, matched_wallet.address, amount_btc, None, confirmations, usd_value,
            )
    finally:
        if owns_session:
            db.close()


async def start_polling_loop():
    """
    يشتغل بالخلفية طول عمر السيرفر — يفحص كل العناوين كل TRON_POLL_INTERVAL_SECONDS.

    NEW: كانت poll_trc20_deposits_once() و poll_bitgo_wallet_once() معرّفتين
    بالأعلى لكن ما توصل أي منهما تُنادى من أي مكان — USDT-TRC20 وBTC ما كانوا
    يُكتشفون فعلياً أبداً رغم وجود كل المنطق. مُضافتين هنا الآن فقط (poll_all_
    wallets_once لـ TRX تبقى بالضبط بنفس السلوك والتوقيت السابق).
    poll_bitgo_wallet_once() تصير no-op تلقائياً لو BitGo مو مُهيأ (شوف تعريفها).
    """
    global _monitor_started
    if _monitor_started:
        print("[blockchain_monitor] polling loop already running in this process, skipping duplicate start")
        return
    _monitor_started = True

    import asyncio
    while True:
        poll_all_wallets_once()
        try:
            poll_trc20_deposits_once()
        except Exception as e:
            print(f"[blockchain_monitor] USDT-TRC20 poll pass failed, will retry next cycle: {e}")
        try:
            poll_bitgo_wallet_once()
        except Exception as e:
            print(f"[blockchain_monitor] BitGo BTC poll pass failed, will retry next cycle: {e}")
        await asyncio.sleep(TRON_POLL_INTERVAL_SECONDS)
