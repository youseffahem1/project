"""
NEW (additive only, admin-only): lets an admin flip a single switch so
THEIR OWN NGN wheel spins can win more than what they played — for
testing/demo purposes. Singleton settings row, editable from
Admin > Settings.

Ironclad scope guarantee: spin_routes.py only ever calls
resolve_admin_boosted_spin() when BOTH of these are true, checked at the
call site in spin_routes.py — never here:
  1. the person currently spinning is themselves an admin (user.is_admin)
  2. get_settings(db).enabled is True
A normal player's spin never reaches this file at all, regardless of this
setting's value. Turning the toggle back OFF (set_enabled(db, False))
means the very next check in spin_routes.py takes the untouched
spin_tier_service.resolve_tiered_spin() branch instead — the exact same
path every normal player always uses, with the exact same
prize <= play_amount hard cap. Nothing about that function changes; this
module never modifies it, only sits beside it.
"""
from sqlalchemy.orm import Session

from . import models


def get_settings(db: Session) -> models.AdminWinBoostSetting:
    settings = db.query(models.AdminWinBoostSetting).first()
    if settings:
        return settings
    settings = models.AdminWinBoostSetting(enabled=False, custom_amount=None)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def set_enabled(db: Session, enabled: bool, admin_id: str = None) -> models.AdminWinBoostSetting:
    settings = get_settings(db)
    settings.enabled = bool(enabled)
    if admin_id:
        settings.updated_by_admin_id = admin_id
    db.commit()
    db.refresh(settings)
    return settings


def set_custom_amount(db: Session, custom_amount, admin_id: str = None) -> models.AdminWinBoostSetting:
    settings = get_settings(db)
    settings.custom_amount = float(custom_amount) if custom_amount not in (None, "") else None
    if admin_id:
        settings.updated_by_admin_id = admin_id
    db.commit()
    db.refresh(settings)
    return settings


def resolve_admin_boosted_spin(db: Session, play_amount: float, custom_amount=None) -> dict:
    """Guaranteed win, deliberately bypassing the normal
    prize <= play_amount cap. Only ever called for an admin with the
    toggle on (see spin_routes.py) — never for a regular player.

    - custom_amount set -> awards exactly that amount every time.
    - custom_amount empty -> awards the single largest prize configured
      anywhere across every active NGN tier (spin_tier_service), so an
      admin doesn't have to type a number to just see "a big win" work.
    - If literally no NGN tiers/prizes are configured yet at all (a fresh
      install), falls back to 2x play_amount — still guaranteed to be a
      win, and still guaranteed to be MORE than what was played, so the
      toggle never silently no-ops even on an empty database.
    """
    from . import spin_tier_service

    if custom_amount is not None and custom_amount > 0:
        prize = float(custom_amount)
    else:
        tiers = spin_tier_service.get_tiers(db, "NGN")
        all_prizes = [pv.prize_amount for t in tiers for pv in t.prizes]
        prize = float(max(all_prizes)) if all_prizes else float(play_amount) * 2

    return {"prize": prize, "table": [(prize, 1.0)], "tier": None}
