"""
واجهات دفتر الأدمن (Admin Ledger) — كلها محمية بـ get_current_admin (JWT + is_admin=True).
لا تلمس أي مسار أو منطق موجود سابقاً؛ كل شي هنا إضافة جديدة بالكامل.
"""
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas, ledger_service
from ..database import get_db
from ..auth import get_current_admin
from ..services import tron_service
from ..config import LEDGER_SUPPORTED_CURRENCIES, SUN_PER_TRX, TRON_ADDRESS_CURRENCY_LABEL

router = APIRouter(prefix="/api/admin/ledger", tags=["admin-ledger"])


def _totals_for_currency(db: Session, currency: str) -> dict:
    ledger = ledger_service.get_or_create_ledger(db, currency)

    total_spin_revenue = (
        db.query(func.coalesce(func.sum(models.LedgerTransaction.amount), 0.0))
        .filter(
            models.LedgerTransaction.currency == currency,
            models.LedgerTransaction.type == models.LedgerTxType.SPIN_FEE,
            models.LedgerTransaction.status == models.LedgerTxStatus.COMPLETED,
        )
        .scalar()
        or 0.0
    )

    total_withdrawals = (
        db.query(func.coalesce(func.sum(models.LedgerTransaction.amount), 0.0))
        .filter(
            models.LedgerTransaction.currency == currency,
            models.LedgerTransaction.type == models.LedgerTxType.ADMIN_WITHDRAWAL,
            models.LedgerTransaction.status == models.LedgerTxStatus.COMPLETED,
        )
        .scalar()
        or 0.0
    )
    total_withdrawals = abs(total_withdrawals)  # stored as negative amounts

    # Total confirmed user deposits (informational — platform-wide, not ledger revenue).
    # Reuses existing deposit records; does not modify or duplicate them.
    total_chain_deposits = (
        db.query(func.coalesce(func.sum(models.BlockchainDeposit.amount_usdt_equivalent), 0.0))
        .filter(
            models.BlockchainDeposit.currency == currency,
            models.BlockchainDeposit.credited == True,  # noqa: E712
        )
        .scalar()
        or 0.0
    )
    total_legacy_deposits = 0.0
    if currency == TRON_ADDRESS_CURRENCY_LABEL:
        # legacy Binance Pay deposits aren't tagged per-currency; only fold them in
        # for the currency our system already treats as the "USD-equivalent" ledger.
        total_legacy_deposits = (
            db.query(func.coalesce(func.sum(models.Deposit.amount_usdt), 0.0))
            .filter(models.Deposit.status == models.DepositStatus.CONFIRMED)
            .scalar()
            or 0.0
        )
    total_deposits = total_chain_deposits + total_legacy_deposits

    return {
        "ledger": ledger,
        "total_deposits": float(total_deposits),
        "total_spin_revenue": float(total_spin_revenue),
        "total_withdrawals": float(total_withdrawals),
    }


@router.get("", response_model=schemas.AdminLedgerOut)
def get_ledger(
    currency: str = TRON_ADDRESS_CURRENCY_LABEL,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    totals = _totals_for_currency(db, currency)
    ledger = totals["ledger"]

    recent_txs = (
        db.query(models.LedgerTransaction)
        .filter(models.LedgerTransaction.currency == currency)
        .order_by(models.LedgerTransaction.created_at.desc())
        .limit(50)
        .all()
    )

    return schemas.AdminLedgerOut(
        currency=currency,
        total_balance=ledger.total_balance,
        total_deposits=totals["total_deposits"],
        total_spin_revenue=totals["total_spin_revenue"],
        total_withdrawals=totals["total_withdrawals"],
        available_balance=ledger.total_balance,
        transactions=recent_txs,
    )


@router.get("/transactions", response_model=list[schemas.LedgerTransactionOut])
def get_ledger_transactions(
    currency: str = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    limit = max(1, min(limit, 500))
    q = db.query(models.LedgerTransaction)
    if currency:
        q = q.filter(models.LedgerTransaction.currency == currency)
    q = q.order_by(models.LedgerTransaction.created_at.desc())
    return q.offset(offset).limit(limit).all()


@router.post("/withdraw")
def admin_withdraw(
    payload: schemas.AdminWithdrawRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    currency = payload.currency.strip()

    # --- Idempotency: block an accidental duplicate submit ---
    if payload.idempotency_key:
        dup = (
            db.query(models.LedgerTransaction)
            .filter_by(reference_id=payload.idempotency_key, type=models.LedgerTxType.ADMIN_WITHDRAWAL)
            .first()
        )
        if dup:
            return {
                "success": True, "message": "Withdrawal already submitted",
                "ledger_tx_id": dup.id, "status": dup.status, "tx_hash": dup.tx_hash,
            }

    if currency not in LEDGER_SUPPORTED_CURRENCIES:
        return {"success": False, "message": "Currency not available yet"}

    try:
        amount = Decimal(str(payload.amount))
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="Invalid amount")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")

    address = payload.destination_address.strip()
    if not tron_service.is_valid_tron_address(address):
        raise HTTPException(status_code=400, detail="Invalid destination address")

    # --- Reserve the ledger balance first (prevents double-spend on retry/race),
    #      mirroring the exact pattern used in /wallet/withdraw ---
    try:
        debit_tx = ledger_service.debit_ledger(
            db, currency=currency, amount=float(amount),
            tx_type=models.LedgerTxType.ADMIN_WITHDRAWAL,
            reference_id=payload.idempotency_key,
            status=models.LedgerTxStatus.PENDING,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Insufficient ledger balance")
    db.commit()
    db.refresh(debit_tx)

    # --- Send the real on-chain TRX using the SAME dedicated withdrawal wallet
    #      already used for user withdrawals. No private keys ever touch the DB
    #      or the response. 1 TRX = 1 ledger unit, same convention already used
    #      by blockchain_monitor.py for deposits. ---
    amount_sun = int(amount * SUN_PER_TRX)
    try:
        result = tron_service.send_trx_withdrawal(
            to_address=address, amount_sun=amount_sun, sequence_id=debit_tx.id,
        )
        debit_tx.tx_hash = result.get("tx_hash")
        debit_tx.status = models.LedgerTxStatus.COMPLETED
        db.commit()
    except tron_service.TronServiceError as e:
        # Refund the ledger since the actual send failed
        ledger_service.credit_ledger(
            db, currency=currency, amount=float(amount),
            tx_type=models.LedgerTxType.ADJUSTMENT, reference_id=debit_tx.id,
            status=models.LedgerTxStatus.COMPLETED,
        )
        debit_tx.status = models.LedgerTxStatus.FAILED
        db.commit()
        raise HTTPException(status_code=502, detail=f"Could not send withdrawal: {e}")

    ledger = ledger_service.get_or_create_ledger(db, currency)
    return {
        "success": True,
        "message": "Withdrawal submitted successfully",
        "ledger_tx_id": debit_tx.id,
        "tx_hash": debit_tx.tx_hash,
        "amount": str(amount),
        "currency": currency,
        "new_ledger_balance": ledger.total_balance,
    }
