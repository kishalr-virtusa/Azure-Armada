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
    else:
        result["email_sent"] = False
    
    return result


def _send_email(kind: NotifyKind, title: str, body: str, link: str | None) -> None:
    """Send an email notification via Azure Communication Services."""
    client = EmailClient.from_connection_string(config.acs_connection_string)
    
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
