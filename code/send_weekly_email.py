"""Send the generated weekly HTML brief through QQ SMTP.

Credentials are read only from environment variables / GitHub Actions secrets.
Never put the QQ authorization code in config.ini or source control.
"""

from __future__ import annotations

import argparse
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def send_weekly_email(html_path: str, md_path: str, json_path: str) -> None:
    sender = os.getenv("QQ_SENDER_EMAIL", "171142515@qq.com").strip()
    receiver = os.getenv("QQ_RECEIVER_EMAIL", "171142515@qq.com").strip()
    password = os.getenv("QQ_SMTP_PASSWORD", "").strip()
    host = os.getenv("QQ_SMTP_HOST", "smtp.qq.com").strip()
    port = int(os.getenv("QQ_SMTP_PORT", "465"))

    if not sender or not receiver:
        raise RuntimeError("QQ sender/receiver email is not configured.")
    if not password:
        raise RuntimeError("QQ_SMTP_PASSWORD is missing. Add the QQ authorization code as a GitHub Actions secret; do not send it in chat.")

    html_file = Path(html_path)
    md_file = Path(md_path)
    json_file = Path(json_path)
    if not html_file.exists() or not md_file.exists() or not json_file.exists():
        raise FileNotFoundError("Weekly report files are incomplete; refusing to send email.")

    msg = MIMEMultipart("mixed")
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = "DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报"

    html_body = html_file.read_text(encoding="utf-8")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for path in (md_file, json_file):
        part = MIMEApplication(path.read_bytes())
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        msg.attach(part)

    with smtplib.SMTP_SSL(host, port, timeout=60) as server:
        server.login(sender, password)
        server.sendmail(sender, [receiver], msg.as_string())

    print(f"[email] weekly brief sent to {receiver}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True)
    parser.add_argument("--md", required=True)
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    send_weekly_email(args.html, args.md, args.json)


if __name__ == "__main__":
    main()
