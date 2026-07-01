"""
api/emailer.py

Sends the password-reset verification code by email.

TEST MODE (default): if no email server is configured, the code is simply
PRINTED to the server console instead of being emailed. This lets you test
the whole reset flow today, without setting up email - you just read the
code from the terminal where uvicorn is running.

REAL MODE: set these environment variables and emails will actually send:
    SMARTIME_SMTP_HOST       e.g. smtp.gmail.com
    SMARTIME_SMTP_PORT       default 587
    SMARTIME_SMTP_USER       the email account username
    SMARTIME_SMTP_PASSWORD   the email account password / app password
    SMARTIME_SMTP_FROM       the "from" address (defaults to SMTP_USER)
"""

import os
import smtplib
from email.message import EmailMessage


def _smtp_configured() -> bool:
    return bool(os.environ.get("SMARTIME_SMTP_HOST"))


def send_reset_code(to_email: str, code: str) -> None:
    subject = "SmarTime password reset code"
    body = (
        f"Your SmarTime verification code is: {code}\n\n"
        "Enter this code on the verification page to continue resetting "
        "your password. It expires in 15 minutes.\n\n"
        "If you did not request this, you can ignore this email."
    )

    if not _smtp_configured():
        # TEST MODE - show the code in the server console.
        print("=" * 50)
        print(f"[SmarTime] Reset code for {to_email}: {code}")
        print("(Email NOT sent - no SMTP configured, this is test mode.)")
        print("=" * 50)
        return

    host = os.environ["SMARTIME_SMTP_HOST"]
    port = int(os.environ.get("SMARTIME_SMTP_PORT", "587"))
    user = os.environ.get("SMARTIME_SMTP_USER")
    password = os.environ.get("SMARTIME_SMTP_PASSWORD")
    sender = os.environ.get("SMARTIME_SMTP_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)
