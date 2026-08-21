"""
NEW FILE — fixes withdrawals staying stuck at PROCESSING forever, and is the
single place the "withdrawal completed" email is triggered from.

ROOT CAUSE of the "stuck on PROCESSING" bug:
Previously, the ONLY place a withdrawal's status moved from PROCESSING to
COMPLETED was inside the GET /api/wallet/withdraw/status/{id} endpoint — i.e.
a client had to actively poll that endpoint for the status to ever update.
If the frontend never called it (tab closed, app backgrounded, no polling
implemented, etc.), the row would sit at PROCESSING indefinitely even though
the transaction had long since confirmed on-chain.

FIX: the same confirmation check that endpoint already did is extracted here
into check_and_finalize(), and a background loop (mirroring
blockchain_monitor.start_polling_loop()) now calls it periodically for every
withdrawal still marked PROCESSING with a tx_hash — regardless of whether a
client is polling. The endpoint now just calls this same shared function.

This file does not change withdrawal creation/sending logic in
routes/wallet_routes.py at all — it only supplies the missing "check TRON,
confirm, and mark COMPLETED" step, and fires the completion email exactly
once when that transition actually happens.
"""
from .database import SessionLocal
from . import models, email_service
from .services import tron_service
from .config import TRON_MIN_CONFIRMATIONS

_monitor_started = False


def check_and_finalize(db, w, user=None):
    """
    Checks a single withdrawal against TRON Nile and, if it has reached the
    required confirmations, marks it COMPLETED and fires the completion email.

    Guarded so the email can only ever fire once per withdrawal:
    - Only runs the TRON lookup at all when status == PROCESSING and a
      tx_hash exists (same precondition as the original inline code).
    - Only sends the email inside the branch that just performed the
      PROCESSING -> COMPLETED transition, so a later call (status already
      COMPLETED) no longer satisfies the "if PROCESSING" guard and is a no-op.
    """
    if w.status != models.WithdrawalStatus.PROCESSING or not w.tx_hash:
        return

    # NEW: a BTC withdrawal's tx_hash is a BitGo/Bitcoin txid, not a TRON one —
    # asking TRON about it would just harmlessly fail every single poll
    # forever. Skip it here rather than doing a pointless lookup. (BitGo
    # withdrawal confirmation polling isn't wired up yet — BTC withdrawals
    # stay at PROCESSING until reconciled; same honest limitation noted in
    # the delivery report.)
    if getattr(w, "currency", None) == "BTC":
        return

    try:
        info = tron_service.get_transaction_info(w.tx_hash)
    except tron_service.TronServiceError:
        return  # keep last known status, don't fail the caller over a status check

    if not (info.get("success") and info.get("confirmations", 0) >= TRON_MIN_CONFIRMATIONS):
        return

    w.status = models.WithdrawalStatus.COMPLETED
    db.commit()

    # Only send the completion email once we've confirmed COMPLETED + a valid
    # tx_hash, right after this exact transition — never for PROCESSING.
    if not w.tx_hash:
        return

    if user is None:
        user = db.query(models.User).filter_by(id=w.user_id).first()
    if not user:
        return

    from datetime import datetime
    try:
        email_service.send_withdrawal_completed_email(
            user_name=user.full_name, user_email=user.email,
            currency="TRX (Nile)", amount=str(w.amount_usdt),
            destination_address=w.address, status=w.status,
            tx_hash=w.tx_hash, event_time=datetime.utcnow(),
        )
    except Exception as e:
        print("[withdrawal_monitor] completion email failed (withdrawal still completed): " + str(e))


def poll_all_withdrawals_once(db=None):
    """Checks every PROCESSING withdrawal with a tx_hash for on-chain confirmation."""
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    try:
        pending = db.query(models.Withdrawal).filter_by(
            status=models.WithdrawalStatus.PROCESSING
        ).all()
        for w in pending:
            if not w.tx_hash:
                continue
            check_and_finalize(db, w)
    finally:
        if owns_session:
            db.close()


async def start_polling_loop():
    """Runs alongside the deposit monitor for the life of the server."""
    global _monitor_started
    if _monitor_started:
        print("[withdrawal_monitor] polling loop already running in this process, skipping duplicate start")
        return
    _monitor_started = True

    import asyncio
    from .config import TRON_POLL_INTERVAL_SECONDS
    while True:
        poll_all_withdrawals_once()
        await asyncio.sleep(TRON_POLL_INTERVAL_SECONDS)
