import os
import time
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# --- Configuration (set as environment variables in Railway) ---
TARGET_URL = "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547"
TARGET_DATE = os.environ.get("TARGET_DATE", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))

SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

SOLD_OUT_SIGNALS = [
    "sold out", "sold-out", "unavailable", "no tickets available",
    "esaurito", "not available", "no availability", "biglietti esauriti",
    "no dates available", "nessuna data"
]
AVAILABLE_SIGNALS = [
    "add to cart", "buy tickets", "book now", "acquista", "purchase",
    "select date", "scegli data", "add to basket", "aggiungi al carrello",
    "buy now", "compra"
]

last_status = None


def check_availability():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        )
        try:
            page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
            # Wait a bit for JS to render
            page.wait_for_timeout(3000)
            content = page.content().lower()
            text = page.inner_text("body").lower()

            if TARGET_DATE and TARGET_DATE.lower() not in text:
                log.info(f"Target date '{TARGET_DATE}' not found on page.")
                browser.close()
                return "date_not_found"

            is_sold_out = any(s in text for s in SOLD_OUT_SIGNALS)
            is_available = any(s in text for s in AVAILABLE_SIGNALS)

            # Also check for calendar/date picker elements (vivaticket specific)
            has_calendar = page.locator("text=Select date").count() > 0 or \
                           page.locator("[class*='calendar']").count() > 0 or \
                           page.locator("[class*='date-picker']").count() > 0

            log.info(f"Sold-out signals found: {is_sold_out} | Available signals: {is_available} | Calendar: {has_calendar}")

            browser.close()

            if (is_available or has_calendar) and not is_sold_out:
                return "available"
            elif is_sold_out:
                return "sold_out"
            else:
                return "unknown"

        except PlaywrightTimeout:
            log.warning("Page timed out")
            browser.close()
            return "error"
        except Exception as e:
            log.error(f"Error checking page: {e}")
            browser.close()
            return "error"


def send_email_alert():
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL]):
        log.error("Email credentials missing — check SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL variables.")
        return

    date_line = f"<p><b>Date watched:</b> {TARGET_DATE}</p>" if TARGET_DATE else ""
    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;">
      <h2 style="color:#3B6D11;">🎨 Tickets may be available!</h2>
      {date_line}
      <p>The availability monitor detected a change on the Cenacolo Vinciano booking page. Act fast!</p>
      <p>
        <a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:10px 20px;
        border-radius:8px;text-decoration:none;font-weight:bold;">
          Book now →
        </a>
      </p>
      <p style="color:#888;font-size:12px;">
        Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC<br>
        Sent by your Cenacolo Vinciano ticket monitor.
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
    log.info(f"URL: {TARGET_URL}")
    log.info(f"Target date: '{TARGET_DATE}' (blank = any availability)")
    log.info(f"Check interval: {CHECK_INTERVAL}s")
    log.info(f"Notifying: {RECIPIENT_EMAIL}")

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
