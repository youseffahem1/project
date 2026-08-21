"""
يرسل إيميل للأدمن (ADMIN_NOTIFICATION_EMAIL / ADMIN_EVENTS_EMAIL) عند signup /
deposit / withdrawal / Nigerian deposit.

UPDATED: كان يستخدم Gmail SMTP (smtplib, port 587) — Render Free يحجب منافذ
SMTP الصادرة بالكامل (25/465/587)، فالإرسال ما كان يشتغل على Render Free نهائياً.
استبدلناه بـ Resend's Email API عبر HTTPS (port 443 العادي) — نفس البروتوكول
اللي المتصفح/الـ API الحالي يستخدمه أصلاً، ما يحتاج أي منفذ إضافي مفتوح.

لا تغيير على: مين يستلم كل إيميل، الـ subject، محتوى النص/الـ HTML، أو نقاط
الاستدعاء (auth_routes.py / blockchain_monitor.py / withdrawal_monitor.py /
wallet_routes.py / nigerian_deposit_routes.py) — فقط طريقة "الإرسال" الفعلية
تغيّرت من SMTP إلى HTTPS API. كل دالة عامة هنا نفس التوقيع (signature) بالضبط.

Get a free Resend API key at https://resend.com — set RESEND_API_KEY and
EMAIL_FROM (a sender address on a domain you verified in Resend, or their
onboarding sender while testing) as environment variables. See config.py.
"""
import base64
import os

import httpx

from .config import (
    RESEND_API_KEY, EMAIL_FROM, ADMIN_NOTIFICATION_EMAIL,
    ADMIN_EVENTS_EMAIL,
)

RESEND_API_URL = "https://api.resend.com/emails"


def _resend_send(to_email, subject, text_body, html_body, attachment=None):
    """Single shared transport: POST to Resend's HTTPS API (443), never SMTP.
    Never raises — same "log and swallow" contract the old smtplib code had,
    so a caller's DB transaction/response can never fail because of email.
    `attachment`, if given, is a dict: {"filename": ..., "path": ...}."""
    if not RESEND_API_KEY or not EMAIL_FROM:
        print("[email_service] RESEND_API_KEY / EMAIL_FROM not configured, skipping email", flush=True)
        print(f"[email_service] RESEND_API_KEY exists: {bool(RESEND_API_KEY)}", flush=True)
        print(f"[email_service] EMAIL_FROM exists: {bool(EMAIL_FROM)}", flush=True)
        return

    payload = {
        "from": EMAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }

    if attachment and attachment.get("path") and os.path.isfile(attachment["path"]):
        try:
            with open(attachment["path"], "rb") as f:
                raw = f.read()
            payload["attachments"] = [{
                "filename": attachment.get("filename") or os.path.basename(attachment["path"]),
                "content": base64.b64encode(raw).decode("ascii"),
            }]
        except Exception as e:
            print("[email_service] could not attach file: " + str(e), flush=True)

    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        if resp.status_code >= 400:
            print(f"[email_service] EMAIL FAILED: HTTP {resp.status_code}: {resp.text}", flush=True)
        else:
            print(f"[email_service] EMAIL SENT SUCCESSFULLY to {to_email} ('{subject}')", flush=True)
    except Exception as e:
        print(f"[email_service] EMAIL FAILED: {type(e).__name__}: {e}", flush=True)


def send_withdrawal_request_email(user_email, user_name, amount_points,
                                    amount_usdt, currency, address, withdrawal_id):
    subject = "New withdrawal request - " + str(amount_usdt) + " " + currency
    body = (
        "New withdrawal request on LuckySpin:\n\n"
        "User: " + user_name + " (" + user_email + ")\n"
        "Amount: " + str(amount_points) + " points (~" + str(amount_usdt) + " " + currency + ")\n"
        "Destination address: " + address + "\n"
        "Withdrawal ID: " + withdrawal_id + "\n\n"
        "Review it before it is auto-processed if it exceeds the auto-withdraw limit.\n"
    )
    _resend_send(ADMIN_NOTIFICATION_EMAIL, subject, body, f"<pre>{body}</pre>")


# =============================================================================
# NEW (added): admin notification emails for signup / confirmed deposit /
# withdrawal completion. These are additive only — nothing above this line
# was changed in meaning, only the transport (see _resend_send above). Each
# function is self-contained and never raises.
# =============================================================================

# --- Design tokens for the premium HTML templates ---------------------------
_BG_PAGE = "#f4f2fb"              # light futuristic lavender-white background
_BG_GLOW_A = "#eaddff"
_BG_GLOW_B = "#fff7e0"
_CARD_BG = "rgba(255, 255, 255, 0.65)"
_CARD_BORDER = "rgba(168, 85, 247, 0.35)"   # neon purple, translucent
_NEON_PURPLE = "#8B5CF6"
_NEON_PURPLE_DARK = "#6D28D9"
_NEON_GOLD = "#F5B841"
_TEXT_DARK = "#241B3A"
_TEXT_MUTED = "#6B6480"

_STATUS_COLORS = {
    "COMPLETED": "#1FA97A",
    "CONFIRMED": "#1FA97A",
    "PROCESSING": "#8B5CF6",
    "PENDING": "#F5B841",
    "PENDING_REVIEW": "#F5B841",
    "FAILED": "#E0507A",
}


def _status_pill(status):
    color = _STATUS_COLORS.get(str(status).upper(), _NEON_PURPLE)
    return (
        f'<span style="display:inline-block;padding:6px 16px;border-radius:999px;'
        f'font-size:13px;font-weight:700;letter-spacing:0.04em;color:#ffffff;'
        f'background:{color};box-shadow:0 0 12px {color}66;">{str(status).upper()}</span>'
    )


def _row(label, value_html):
    return f"""
      <tr>
        <td style="padding:12px 0;border-bottom:1px solid rgba(139,92,246,0.12);width:38%;
                   font-size:13px;font-weight:600;color:{_TEXT_MUTED};letter-spacing:0.03em;
                   text-transform:uppercase;vertical-align:top;">
          {label}
        </td>
        <td style="padding:12px 0;border-bottom:1px solid rgba(139,92,246,0.12);
                   font-size:15px;font-weight:600;color:{_TEXT_DARK};word-break:break-all;
                   vertical-align:top;">
          {value_html}
        </td>
      </tr>"""


def _render_email_html(eyebrow, headline, subheadline, rows_html, footer_note):
    """Shared LuckySpin premium HTML shell: logo header, light futuristic
    background, glassmorphism card, neon purple/gold accents, mobile
    responsive (single fluid column, max-width 600px)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LuckySpin</title>
</head>
<body style="margin:0;padding:0;background:{_BG_PAGE};
             font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:radial-gradient(circle at 15% 0%, {_BG_GLOW_A} 0%, transparent 45%),
                        radial-gradient(circle at 100% 20%, {_BG_GLOW_B} 0%, transparent 40%),
                        {_BG_PAGE}; padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="max-width:600px;width:100%;">

          <!-- Logo header -->
          <tr>
            <td align="center" style="padding:6px 0 22px 0;">
              <span style="font-size:26px;font-weight:800;letter-spacing:0.02em;
                           background:linear-gradient(90deg,{_NEON_PURPLE} 0%,{_NEON_GOLD} 100%);
                           -webkit-background-clip:text;background-clip:text;
                           color:{_NEON_PURPLE_DARK};">
                ⚡ LuckySpin
              </span>
            </td>
          </tr>

          <!-- Glassmorphism card -->
          <tr>
            <td style="background:{_CARD_BG};border:1px solid {_CARD_BORDER};
                       border-radius:24px;padding:36px 30px;
                       box-shadow:0 8px 32px rgba(109,40,217,0.14),
                                  0 0 0 1px rgba(255,255,255,0.4) inset;">

              <div style="font-size:12px;font-weight:700;letter-spacing:0.12em;
                          text-transform:uppercase;color:{_NEON_PURPLE_DARK};margin-bottom:10px;">
                {eyebrow}
              </div>
              <div style="font-size:22px;font-weight:800;color:{_TEXT_DARK};margin-bottom:6px;">
                {headline}
              </div>
              <div style="font-size:14px;color:{_TEXT_MUTED};margin-bottom:22px;">
                {subheadline}
              </div>

              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {rows_html}
              </table>

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td align="center" style="padding:22px 12px 0 12px;">
              <div style="font-size:12px;color:{_TEXT_MUTED};line-height:1.6;">
                {footer_note}<br>
                This is an automated admin notification from LuckySpin.
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _send_admin_event_email(subject, text_body, html_body):
    _resend_send(ADMIN_EVENTS_EMAIL, subject, text_body, html_body)


def send_registration_email(user_name, user_email, signup_time=None):
    """Fired once, right after a new user row is committed in auth_routes.signup()."""
    when = signup_time.strftime("%Y-%m-%d %H:%M:%S UTC") if signup_time else ""
    subject = "New user registered - " + user_name

    text_body = (
        "A new user has registered on LuckySpin:\n\n"
        "Name: " + user_name + "\n"
        "Email: " + user_email + "\n"
        "Date/time: " + when + "\n"
    )

    rows_html = (
        _row("User", user_name)
        + _row("Email", user_email)
        + _row("Date", when)
    )
    html_body = _render_email_html(
        eyebrow="New Account",
        headline="🎉 A new player just joined",
        subheadline="A new account was created on LuckySpin.",
        rows_html=rows_html,
        footer_note="Registration notification.",
    )

    _send_admin_event_email(subject, text_body, html_body)


def send_deposit_confirmed_email(user_name, user_email, currency, amount,
                                  deposit_address, sender_address, tx_hash,
                                  block_number, confirmations, confirmed_time=None):
    """Fired once per tx_hash, from blockchain_monitor._credit_deposit(), only
    after the existing idempotency check has passed and the deposit/points have
    already been committed to the database."""
    when = confirmed_time.strftime("%Y-%m-%d %H:%M:%S UTC") if confirmed_time else ""
    subject = "Deposit confirmed - " + str(amount) + " " + str(currency)

    text_body = (
        "A deposit has been confirmed and credited on LuckySpin:\n\n"
        "User name: " + str(user_name) + "\n"
        "User email: " + str(user_email) + "\n"
        "Currency: " + str(currency) + "\n"
        "Amount: " + str(amount) + "\n"
        "Deposit address: " + str(deposit_address) + "\n"
        "Sender address: " + str(sender_address) + "\n"
        "Transaction hash: " + str(tx_hash) + "\n"
        "Block number: " + str(block_number) + "\n"
        "Confirmations: " + str(confirmations) + "\n"
        "Date/time: " + when + "\n"
    )

    rows_html = (
        _row("User", f"{user_name}<br><span style='font-weight:400;color:{_TEXT_MUTED};font-size:13px;'>{user_email}</span>")
        + _row("Amount", f"{amount} <span style='color:{_NEON_PURPLE_DARK};'>{currency}</span>")
        + _row("Currency", currency)
        + _row("Network", "TRON Mainnet")
        + _row("Deposit address", f"<span style='font-family:monospace;font-size:13px;'>{deposit_address}</span>")
        + _row("Sender address", f"<span style='font-family:monospace;font-size:13px;'>{sender_address}</span>")
        + _row("Transaction hash", f"<span style='font-family:monospace;font-size:13px;'>{tx_hash}</span>")
        + _row("Block number", str(block_number))
        + _row("Confirmations", str(confirmations))
        + _row("Status", _status_pill("CONFIRMED"))
        + _row("Date", when)
    )
    html_body = _render_email_html(
        eyebrow="Deposit Confirmed",
        headline="💰 Deposit confirmed & credited",
        subheadline="This deposit has cleared on-chain and points were credited.",
        rows_html=rows_html,
        footer_note="Deposit confirmation notification.",
    )

    _send_admin_event_email(subject, text_body, html_body)


def send_withdrawal_completed_email(user_name, user_email, currency, amount,
                                     destination_address, status, tx_hash=None,
                                     event_time=None):
    """Fired ONLY when a withdrawal has been confirmed COMPLETED with a valid
    tx_hash — see withdrawal_monitor.check_and_finalize(), the single place
    this is called from. Never fires for PROCESSING or PENDING_REVIEW."""
    when = event_time.strftime("%Y-%m-%d %H:%M:%S UTC") if event_time else ""
    subject = "Withdrawal " + str(status) + " - " + str(amount) + " " + str(currency)

    text_body = (
        "A withdrawal has completed on LuckySpin:\n\n"
        "User name: " + str(user_name) + "\n"
        "User email: " + str(user_email) + "\n"
        "Currency: " + str(currency) + "\n"
        "Amount: " + str(amount) + "\n"
        "Destination address: " + str(destination_address) + "\n"
        "Transaction hash: " + (str(tx_hash) if tx_hash else "N/A (not yet available)") + "\n"
        "Status: " + str(status) + "\n"
        "Date/time: " + when + "\n"
    )

    rows_html = (
        _row("User", f"{user_name}<br><span style='font-weight:400;color:{_TEXT_MUTED};font-size:13px;'>{user_email}</span>")
        + _row("Amount", f"{amount} <span style='color:{_NEON_PURPLE_DARK};'>{currency}</span>")
        + _row("Currency", currency)
        + _row("Network", "TRON Mainnet")
        + _row("Destination address", f"<span style='font-family:monospace;font-size:13px;'>{destination_address}</span>")
        + _row("Transaction hash", f"<span style='font-family:monospace;font-size:13px;'>{tx_hash if tx_hash else 'N/A'}</span>")
        + _row("Status", _status_pill(status))
        + _row("Date", when)
    )
    html_body = _render_email_html(
        eyebrow="Withdrawal Completed",
        headline="✅ Withdrawal completed",
        subheadline="This withdrawal was confirmed on-chain and has completed.",
        rows_html=rows_html,
        footer_note="Withdrawal completion notification.",
    )

    _send_admin_event_email(subject, text_body, html_body)


# =========================================================================
# NEW: Nigerian Bank Transfer deposit notifications — additive only, does
# not modify _send_admin_event_email() or any function above. Reuses the
# exact same premium HTML template helpers (_row, _status_pill,
# _render_email_html) and the same _resend_send() HTTPS transport.
# =========================================================================

def send_nigerian_deposit_admin_email(user_name, user_email, amount_ngn, bank_name,
                                       account_name, account_number, deposit_id,
                                       created_time=None, proof_path=None, proof_filename=None):
    """Fired once, right after a NigerianDeposit row is committed as PENDING
    in nigerian_deposit_routes.create_nigerian_deposit(). Attaches the proof
    screenshot when the file is readable; otherwise the email still sends
    with the Deposit ID so the admin can open the proof from the admin panel."""
    when = created_time.strftime("%Y-%m-%d %H:%M:%S UTC") if created_time else ""
    subject = "LuckySpin — New Nigerian Deposit (" + str(amount_ngn) + " NGN)"

    text_body = (
        "LuckySpin — New Nigerian Deposit\n\n"
        "User Name: " + str(user_name) + "\n"
        "User Email: " + str(user_email) + "\n"
        "Amount: " + str(amount_ngn) + "\n"
        "Currency: NGN\n\n"
        "Bank: " + str(bank_name) + "\n"
        "Account Name: " + str(account_name) + "\n"
        "Account Number: " + str(account_number) + "\n\n"
        "Deposit ID: " + str(deposit_id) + "\n"
        "Date/Time: " + when + "\n"
        "Status: PENDING\n"
    )

    rows_html = (
        _row("User", f"{user_name}<br><span style='font-weight:400;color:{_TEXT_MUTED};font-size:13px;'>{user_email}</span>")
        + _row("Amount", f"{amount_ngn} <span style='color:{_NEON_PURPLE_DARK};'>NGN</span>")
        + _row("Bank", str(bank_name))
        + _row("Account Name", str(account_name))
        + _row("Account Number", f"<span style='font-family:monospace;font-size:13px;'>{account_number}</span>")
        + _row("Deposit ID", f"<span style='font-family:monospace;font-size:13px;'>{deposit_id}</span>")
        + _row("Status", _status_pill("PENDING"))
        + _row("Date", when)
    )
    html_body = _render_email_html(
        eyebrow="Nigerian Deposit",
        headline="🇳🇬 New Nigerian bank transfer deposit",
        subheadline="A user submitted a manual bank transfer with payment proof — review it in the Admin panel.",
        rows_html=rows_html,
        footer_note="Nigerian deposit notification. Open the Admin Ledger → Deposit Requests panel to view the proof and approve or reject.",
    )

    # Build a friendly attachment filename from the stored file's extension
    # (already validated as jpg/jpeg/png/webp at upload time in
    # nigerian_deposit_routes.create_nigerian_deposit).
    ext = (os.path.splitext(proof_filename or "")[1] or "").lower().lstrip(".")
    friendly_name = f"payment-proof-{deposit_id}.{'jpg' if ext == 'jpeg' else (ext or 'jpg')}"

    _resend_send(
        ADMIN_EVENTS_EMAIL, subject, text_body, html_body,
        attachment={"path": proof_path, "filename": friendly_name} if proof_path else None,
    )
