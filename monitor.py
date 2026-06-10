import os
import time
import logging
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- Configuration ---
TARGET_URL = "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547"
API_URL = "https://www.vivaticket.com/it/api/event/dates?eventId=151991"
TARGET_DATE = os.environ.get("TARGET_DATE", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://cenacolovinciano.vivaticket.it/",
}

last_status = None


def check_availability():
    # Strategy 1: try the vivaticket API for available dates
    try:
        api_resp = requests.get(
            API_URL,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=20
        )
        if api_resp.status_code == 200:
            data = api_resp.json()
            log.info(f"API response: {str(data)[:300]}")
            # Look for any dates with availability
            dates = data if isinstance(data, list) else data.get("dates", data.get("data", []))
            if isinstance(dates, list) and len(dates) > 0:
                for d in dates:
                    availability = str(d).lower()
                    if TARGET_DATE and TARGET_DATE.lower() not in availability:
                        continue
                    if any(x in availability for x in ["available", "true", "1"]):
                        return "available"
                    if any(x in availability for x in ["sold", "false", "0", "esaurito"]):
                        continue
                log.info("API returned dates — checking page as fallback")
    except Exception as e:
        log.info(f"API check skipped: {e}")

    # Strategy 2: fetch the main page HTML
    try:
        resp = requests.get(TARGET_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text
        text = html.lower()

        log.info(f"Page fetched: {len(html)} bytes")

        # Log a snippet around key terms for debugging
        for keyword in ["esaurito", "sold", "acquista", "available", "cart", "date", "calendar"]:
            idx = text.find(keyword)
            if idx != -1:
                snippet = text[max(0, idx-30):idx+50].replace("\n", " ")
                log.info(f"  Found '{keyword}': ...{snippet}...")

        sold_signals = ["sold out", "esaurito", "biglietti esauriti", "not available",
                        "no availability", "nessuna data disponibile"]
        avail_signals = ["add to cart", "acquista", "aggiungi al carrello",
                         "buy now", "compra ora", "select", "book now"]

        is_sold = any(s in text for s in sold_signals)
        is_avail = any(s in text for s in avail_signals)

        if is_avail and not is_sold:
            return "available"
        elif is_sold:
            return "sold_out"
        else:
            # Log 500 chars of body text for diagnosis
            import re
            body_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))[:500]
            log.info(f"Page text sample: {body_text}")
            return "unknown"

    except Exception as e:
        log.error(f"Page fetch failed: {e}")
        return "error"


def send_email_alert():
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL]):
        log.error("Email credentials missing.")
        return

    date_line = f"<p><b>Date watched:</b> {TARGET_DATE}</p>" if TARGET_DATE else ""
    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;">
      <h2 style="color:#3B6D11;">🎨 Tickets may be available!</h2>
      {date_line}
      <p>The monitor detected a change on the Cenacolo Vinciano booking page. Act fast!</p>
      <p>
        <a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:10px 20px;
        border-radius:8px;text-decoration:none;font-weight:bold;">Book now →</a>
      </p>
      <p style="color:#888;font-size:12px;">
        Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC
      </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🎨 Cenacolo Vinciano — Tickets Available!"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        log.info(f"✅ Alert email sent to {RECIPIENT_EMAIL}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def main():
    global last_status
    log.info("=== Cenacolo Vinciano Ticket Monitor Started ===")
    log.info(f"Notifying: {RECIPIENT_EMAIL}")
    log.info(f"Check interval: {CHECK_INTERVAL}s")

    while True:
        log.info("Checking availability...")
        status = check_availability()
        log.info(f"Status: {status}")

        if status == "available" and last_status != "available":
            log.info("*** TICKETS AVAILABLE — sending alert! ***")
            send_email_alert()

        last_status = status
        log.info(f"Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
