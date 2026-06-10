import os
import re
import time
import logging
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- Configuration ---
TARGET_URL = "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547"
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "1800"))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

last_status = None

# These indicate the WHOLE event is sold out (no dates at all)
SOLD_SIGNALS = [
    "esaurito", "biglietti esauriti", "nessuna data disponibile",
    "no dates available", "evento non disponibile"
]

# These indicate at least one date has a buyable ticket
# Must be action-oriented (a button/link), not just page text
AVAIL_SIGNALS = [
    "aggiungi al carrello",
    "add to cart",
    "compra ora",
    "buy now",
    "acquista ora",
]

# These are date-slot level sold-out markers — presence alone doesn't mean
# the whole event is unavailable
SLOT_SOLD_SIGNALS = [
    "sold out",
    "not available",
    "no availability",
]


def fetch_via_scraperapi():
    url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={TARGET_URL}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text


def fetch_direct():
    session = requests.Session()
    session.get("https://cenacolovinciano.vivaticket.it/", timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    })
    time.sleep(2)
    resp = session.get(TARGET_URL, timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
        "Referer": "https://cenacolovinciano.vivaticket.it/",
    })
    resp.raise_for_status()
    return resp.text


def analyze(html):
    text = html.lower()
    log.info(f"Page size: {len(html)} bytes")

    # Log all relevant keywords found with context
    for keyword in ["esaurito", "sold out", "acquista", "add to cart", "compra",
                    "aggiungi", "buy now", "not available", "disponib"]:
        idx = 0
        while True:
            idx = text.find(keyword, idx)
            if idx == -1:
                break
            snippet = text[max(0, idx-50):idx+80].replace("\n", " ").strip()
            log.info(f"  '{keyword}': ...{snippet}...")
            idx += len(keyword)

    # Check for action signals (buttons to actually buy)
    is_avail = any(s in text for s in AVAIL_SIGNALS)

    # Check for hard sold-out (whole event unavailable)
    is_hard_sold = any(s in text for s in SOLD_SIGNALS)

    log.info(f"  -> Buy button found: {is_avail} | Hard sold-out: {is_hard_sold}")

    if is_avail and not is_hard_sold:
        return "available"
    elif is_hard_sold and not is_avail:
        return "sold_out"
    elif not is_avail and not is_hard_sold:
        # Dump a text sample to help diagnose
        sample = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))[:600]
        log.info(f"Text sample: {sample}")
        return "unknown"
    else:
        # Both signals present — some dates available, some not
        return "available"


def check_availability():
    methods = [
        ("ScraperAPI", fetch_via_scraperapi),
        ("Direct", fetch_direct),
    ]
    for name, fn in methods:
        try:
            log.info(f"Trying {name}...")
            html = fn()
            if html and len(html) > 500:
                log.info(f"{name} succeeded")
                return analyze(html)
            else:
                log.warning(f"{name} too small ({len(html) if html else 0} bytes)")
        except Exception as e:
            log.warning(f"{name} failed: {e}")
    log.error("All fetch methods failed")
    return "error"


def send_email_alert():
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL]):
        log.error("Email credentials missing.")
        return

    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;">
      <h2 style="color:#3B6D11;">🎨 Tickets are available!</h2>
      <p>The monitor detected availability on the Cenacolo Vinciano booking page. Act fast — these go quickly!</p>
      <p>
        <a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:12px 24px;
        border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;">Book now →</a>
      </p>
      <p style="color:#888;font-size:12px;">Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
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
        log.info(f"Alert email sent to {RECIPIENT_EMAIL}")
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
