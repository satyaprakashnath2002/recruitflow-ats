import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings, get_settings


class EmailDeliveryUnavailable(RuntimeError):
    pass


class EmailSender(Protocol):
    def send(self, recipient: str, subject: str, body: str) -> None: ...


class SMTPEmailSender:
    """Sends only transactional employee email; message contents are never logged."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(self, recipient: str, subject: str, body: str) -> None:
        config = self.settings
        if not config.smtp_host or not config.smtp_from_email:
            raise EmailDeliveryUnavailable("Transactional email is not configured")

        message = EmailMessage()
        message["From"] = config.smtp_from_email
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10) as smtp:
            if config.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if config.smtp_username:
                password = config.smtp_password.get_secret_value() if config.smtp_password else ""
                smtp.login(config.smtp_username, password)
            smtp.send_message(message)
