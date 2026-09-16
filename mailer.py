import asyncio
import smtplib
from email.mime.text import MIMEText

import config


def _send_sync(to_email: str, code: str):
    subject = f"{code} — код входа в VastAnime"
    body = (
        f"Ваш код подтверждения VastAnime: {code}\n\n"
        "Код действителен 10 минут. Если это были не вы — "
        "просто проигнорируйте это письмо."
    )
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{config.SMTP_FROM_NAME} <{config.SMTP_USER}>"
    msg["To"] = to_email

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_USER, [to_email], msg.as_string())


async def send_code_email(to_email: str, code: str):
    await asyncio.to_thread(_send_sync, to_email, code)
