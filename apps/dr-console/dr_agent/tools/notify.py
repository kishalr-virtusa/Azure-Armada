"""Notification operations (console + email)."""

import logging
from typing import Literal

from azure.communication.email import EmailClient

from dr_agent.config import config
from dr_agent.tools import store

logger = logging.getLogger(__name__)

NotifyKind = Literal["info", "action", "success", "failure"]


def notify(
    kind: NotifyKind,
    title: str,
    body: str,
    link: str | None = None,
    exercise_id: str | None = None,
    send_email: bool = True,
) -> dict:
    """Send a notification to the console and optionally email.
    
    Args:
        kind: Notification type (info, action, success, failure)
        title: Short title
        body: Longer description
        link: Optional URL to include
        exercise_id: Optional exercise ID for context
        send_email: Whether to send email (only for important notifications)
    
    Returns:
        dict with notification_id and email_sent status
    """
    result = {"kind": kind, "title": title}
    
    # Always add to console notifications
    try:
        notif_id = store.add_notification(
            kind=kind,
            title=title,
            body=body,
            link=link,
            exercise_id=exercise_id,
        )
        result["notification_id"] = notif_id
        logger.info(f"Notification added: [{kind}] {title}")
    except Exception as e:
        logger.error(f"Failed to add notification: {e}")
        result["notification_error"] = str(e)
    
    # Send email for important notifications
    email_kinds = {"action", "success", "failure"}
    if send_email and kind in email_kinds and config.notify_email_to and config.acs_connection_string:
        try:
            _send_email(kind, title, body, link)
            result["email_sent"] = True
            logger.info(f"Email sent to {config.notify_email_to}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            result["email_sent"] = False
            result["email_error"] = str(e)
    elif send_email and kind in email_kinds and config.notify_email_to and _resend_key():
        try:
            _send_via_resend(kind, title, body, link)
            result["email_sent"] = True
            result["email_channel"] = "resend"
        except Exception as e:
            logger.error(f"Resend email failed, falling back to Azure Monitor: {e}")
            result["email_error"] = str(e)
            result["email_sent"] = _try_azure_monitor(kind, title, body, link, exercise_id)
    elif send_email and kind in email_kinds and config.notify_email_to and config.appinsights_connection_string:
        try:
            _send_via_azure_monitor(kind, title, body, link, exercise_id)
            result["email_sent"] = True
            result["email_channel"] = "azure-monitor"
        except Exception as e:
            logger.error(f"Failed to queue Azure Monitor email: {e}")
            result["email_sent"] = False
            result["email_error"] = str(e)
    else:
        result["email_sent"] = False
    
    return result


def _resend_key() -> str:
    import os
    return os.getenv("RESEND_API_KEY", "").strip()


def _try_azure_monitor(kind, title, body, link, exercise_id) -> bool:
    if not config.appinsights_connection_string:
        return False
    try:
        _send_via_azure_monitor(kind, title, body, link, exercise_id)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"Azure Monitor email failed: {e}")
        return False


def _send_via_resend(kind: str, title: str, body: str, link: str | None) -> None:
    """Instant email (seconds) via the Resend API. Without a verified domain Resend only delivers to the
    account owner's address, which is our DR team inbox (NOTIFY_EMAIL_TO)."""
    import os

    import httpx

    host = os.getenv("WEBSITE_HOSTNAME", "")
    full_link = f"https://{host}{link}" if link and host and link.startswith("/") else link
    html = _build_html(kind, title, body, full_link)
    r = httpx.post("https://api.resend.com/emails", timeout=10,
                   headers={"Authorization": f"Bearer {_resend_key()}"},
                   json={"from": os.getenv("RESEND_FROM", "Virtusa DR Agent <onboarding@resend.dev>"),
                         "to": [a.strip() for a in config.notify_email_to.split(",") if a.strip()],
                         "subject": f"[DR Agent] {title}", "html": html})
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")


def _parse_connection_string(cs: str) -> dict[str, str]:
    return {k.strip().lower(): v.strip() for k, v in (p.split("=", 1) for p in cs.split(";") if "=" in p)}


def _send_via_azure_monitor(kind: str, title: str, body: str, link: str | None, exercise_id: str | None) -> None:
    """Emit a DRNotify custom event to Application Insights; the 'DR Agent notification' log alert emails it
    to the DR team through an Azure Monitor action group (infra/modules/alerts.bicep). ~1-3 min latency."""
    import os
    from datetime import datetime, timezone

    import httpx

    cs = _parse_connection_string(config.appinsights_connection_string)
    ingest = cs.get("ingestionendpoint", "https://dc.services.visualstudio.com/").rstrip("/")
    host = os.getenv("WEBSITE_HOSTNAME", "")
    full_link = f"https://{host}{link}" if link and host and link.startswith("/") else (link or "")
    envelope = [{
        "name": "Microsoft.ApplicationInsights.Event",
        "time": datetime.now(timezone.utc).isoformat(),
        "iKey": cs["instrumentationkey"],
        "data": {"baseType": "EventData", "baseData": {"ver": 2, "name": "DRNotify", "properties": {
            "kind": kind, "title": title[:120], "body": body[:400], "link": full_link,
            "exercise_id": exercise_id or ""}}},
    }]
    r = httpx.post(f"{ingest}/v2.1/track", json=envelope, timeout=10)
    r.raise_for_status()


def _build_html(kind: str, title: str, body: str, link: str | None) -> str:
    """Branded HTML email body shared by all channels."""
    # Determine styling based on kind
    colors = {
        "info": ("#2F5FB3", "#DCE7FA"),
        "action": ("#9A6400", "#FDF0D5"),
        "success": ("#0A7A48", "#D8F2E4"),
        "failure": ("#C0392B", "#FBE3E0"),
    }
    text_color, bg_color = colors.get(kind, colors["info"])
    
    # Build HTML email
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: 'Segoe UI', system-ui, sans-serif;
                line-height: 1.5;
                color: #161B28;
                margin: 0;
                padding: 20px;
                background: #F7F7F9;
            }}
            .card {{
                max-width: 500px;
                margin: 0 auto;
                background: #FFFFFF;
                border: 1px solid #E3E3E8;
                border-radius: 10px;
                padding: 24px;
            }}
            .badge {{
                display: inline-block;
                font-size: 10px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                padding: 4px 8px;
                border-radius: 4px;
                background: {bg_color};
                color: {text_color};
                margin-bottom: 12px;
            }}
            h1 {{
                font-size: 20px;
                font-weight: 600;
                margin: 0 0 12px 0;
            }}
            p {{
                margin: 0 0 16px 0;
                color: #4A4F5E;
            }}
            .btn {{
                display: inline-block;
                background: #5E2CFE;
                color: white;
                text-decoration: none;
                padding: 10px 20px;
                border-radius: 8px;
                font-weight: 600;
                font-size: 14px;
            }}
            .footer {{
                margin-top: 24px;
                font-size: 12px;
                color: #7C8190;
                text-align: center;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="badge">{kind.upper()}</div>
            <h1>🛡️ {title}</h1>
            <p>{body}</p>
            {f'<a href="{link}" class="btn">Open Console</a>' if link else ''}
            <div class="footer">
                DR Agent Console — Virtusa Azure Day 2026
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


def _send_email(kind: NotifyKind, title: str, body: str, link: str | None) -> None:
    """Send an email notification via Azure Communication Services."""
    client = EmailClient.from_connection_string(config.acs_connection_string)
    html_content = _build_html(kind, title, body, link)

    message = {
        "senderAddress": config.acs_sender,
        "recipients": {
            "to": [{"address": config.notify_email_to}],
        },
        "content": {
            "subject": f"[DR Agent] {title}",
            "html": html_content,
        },
    }
    
    poller = client.begin_send(message)
    poller.result()  # Wait for send to complete


# ============== CLI for testing ==============

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Notification CLI")
    parser.add_argument("--kind", choices=["info", "action", "success", "failure"], default="info")
    parser.add_argument("--title", required=True, help="Notification title")
    parser.add_argument("--body", required=True, help="Notification body")
    parser.add_argument("--link", help="Optional link")
    parser.add_argument("--no-email", action="store_true", help="Skip email")
    
    args = parser.parse_args()
    
    result = notify(
        kind=args.kind,
        title=args.title,
        body=args.body,
        link=args.link,
        send_email=not args.no_email,
    )
    print(result)
