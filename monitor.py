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

SOLD_SIGNALS = [
    "sold out", "esaurito", "biglietti esauriti", "not available",
    "no availability", "nessuna data disponibile", "no dates available"
]
AVAIL_SIGNALS = [
    "add to cart", "acquista", "aggiungi al carrello",
    "buy now", "compra ora", "book now", "select date", "scegli data"
]


def fetch_via_scraperapi():
    """Use ScraperAPI without render=true (1 credit per call, not 5)."""
    url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={TARGET_URL}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text


def fetch_via_zenrows():
    """ZenRows free tier alternative — 1000 free credits."""
    zenrows_key = os.environ.get("ZENROWS_API_KEY", "")
    if not zenrows_key:
        return None
    url = f"https://api.zenrows.com/v1/?apikey={zenrows_key}&url={TARGET_URL}&js_render=true"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text


def fetch_direct():
    """Try direct fetch with realistic browser headers."""
    session = requests.Session()
    # First visit the homepage to get cookies
    session.get("https://cenacolovinciano.vivaticket.it/", timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    time.sleep(2)
    resp = session.get(TARGET_URL, timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://cenacolovinciano.vivaticket.it/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    resp.raise_for_status()
    return resp.text


def analyze(html):
    """Parse HTML and return availability status."""
    text = html.lower()
    log.info(f"Page size: {len(html)} bytes")

    for keyword in ["esaurito", "sold", "acquista", "available", "cart", "compra", "book", "biglietti"]:
        idx = text.find(keyword)
        if idx != -1:
            snippet = text[max(0, idx-40):idx+60].replace("\n", " ").strip()
            log.info(f"  '{keyword}': ...{snippet}...")

    is_sold = any(s in text for s in SOLD_SIGNALS)
    is_avail = any(s in text for s in AVAIL_SIGNALS)

    if is_avail and not is_sold:
        return "available"
    elif is_sold:
        return "sold_out"
    else:
        sample = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))[:400]
        log.info(f"Text sample: {sample}")
        return "unknown"


def check_availability():
    # Try each method in order
    methods = [
        ("ScraperAPI", fetch_via_scraperapi),
        ("ZenRows", fetch_via_zenrows),
        ("Direct", fetch_direct),
    ]

    for name, fetch_fn in methods:
        try:
            log.info(f"Trying {name}...")
            html = fetch_fn()
            if html and len(html) > 500:
                log.info(f"{name} succeeded ({len(html)} bytes)")
                return analyze(html)
            else:
                log.warning(f"{name} returned too little content ({len(html) if html else 0} bytes)")
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
      <h2 style="color:#3B6D11;">Tickets may be available!</h2>
      <p>The monitor detected availability on the Cenacolo Vinciano booking page. Act fast!</p>
      <p>
        <a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:10px 20px;
        border-radius:8px;text-decoration:none;font-weight:bold;">Book now</a>
      </p>
      <p style="color:#888;font-size:12px;">Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Cenacolo Vinciano — Tickets Available!"
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
