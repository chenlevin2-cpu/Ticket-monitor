import os
import time
import logging
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from datetime import datetime

# --- Configuration (set these as environment variables) ---
TARGET_URL = "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547"
TARGET_DATE = os.environ.get("TARGET_DATE", "")          # e.g. "September 15" or leave blank for any
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))  # seconds (default: 5 min)

SENDER_EMAIL = os.environ.get("SENDER_EMAIL")            # your Gmail address
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")      # Gmail App Password (not your login password)
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")      # where to send alerts

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SOLD_OUT_SIGNALS = [
    "sold out", "sold-out", "unavailable", "no tickets available",
    "esaurito", "not available", "no availability", "biglietti esauriti"
]
AVAILABLE_SIGNALS = [
    "add to cart", "buy tickets", "book now", "acquista", "purchase",
    "select date", "scegli data", "add to basket"
]

last_status = None  # track to avoid duplicate alerts


def check_availability():
    global last_status
    try:
        resp = requests.get(TARGET_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text(separator=" ").lower()

        # If a target date is specified, check it's present
        if TARGET_DATE and TARGET_DATE.lower() not in page_text:
            log.info(f"Target date '{TARGET_DATE}' not found on page yet.")
            return "date_not_found"

        is_sold_out = any(s in page_text for s in SOLD_OUT_SIGNALS)
        is_available = any(s in page_text for s in AVAILABLE_SIGNALS)

        if is_available and not is_sold_out:
            return "available"
        elif is_sold_out:
            return "sold_out"
        else:
            return "unknown"

    except requests.RequestException as e:
        log.error(f"Request failed: {e}")
        return "error"


def send_email_alert(status):
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL]):
        log.error("Email credentials not set. Check environment variables.")
        return

    subject = "🎨 Cenacolo Vinciano — Tickets Available!" if status == "available" \
        else "⚠️ Cenacolo Vinciano — Monitor Alert"

    date_line = f"<p><b>Date watched:</b> {TARGET_DATE}</p>" if TARGET_DATE else ""

    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;">
      <h2 style="color:#3B6D11;">🎨 Tickets may be available!</h2>
      {date_line}
      <p>The availability monitor detected a change on the Cenacolo Vinciano booking page.</p>
      <p>
        <a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:10px 20px;
        border-radius:8px;text-decoration:none;font-weight:bold;">
          Book now →
        </a>
      </p>
      <p style="color:#888;font-size:12px;">
        Checked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC<br>
        This alert was sent by your ticket monitor script.
      </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        log.info(f"Alert email sent to {RECIPIENT_EMAIL}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def main():
    log.info("=== Cenacolo Vinciano Ticket Monitor Started ===")
    log.info(f"URL: {TARGET_URL}")
    log.info(f"Target date: '{TARGET_DATE}' (blank = any availability)")
    log.info(f"Check interval: {CHECK_INTERVAL}s")
    log.info(f"Notifying: {RECIPIENT_EMAIL}")

    global last_status

    while True:
        log.info("Checking availability...")
        status = check_availability()
        log.info(f"Status: {status}")

        if status == "available" and last_status != "available":
            log.info("*** TICKETS AVAILABLE — sending alert! ***")
            send_email_alert(status)

        last_status = status

        log.info(f"Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
