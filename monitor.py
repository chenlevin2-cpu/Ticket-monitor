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
ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

last_status = None

SOLD_SIGNALS = [
    "esaurito", "biglietti esauriti", "nessuna data disponibile",
    "no dates available", "evento non disponibile"
]
AVAIL_SIGNALS = [
    "aggiungi al carrello", "add to cart",
    "compra ora", "buy now", "acquista ora",
]


def fetch_via_zenrows():
    """ZenRows with JS rendering — free tier: 1000 credits, JS render = 5 credits each = 200 checks."""
    params = {
        "apikey": ZENROWS_API_KEY,
        "url": TARGET_URL,
        "js_render": "true",
        "wait": "3000",  # wait 3s for JS to load
    }
    resp = requests.get("https://api.zenrows.com/v1/", params=params, timeout=60)
    resp.raise_for_status()
    return resp.text


def fetch_via_scraperapi_rendered():
    """ScraperAPI with render=true as fallback."""
    scraper_key = os.environ.get("SCRAPER_API_KEY", "")
    if not scraper_key:
        raise Exception("No SCRAPER_API_KEY set")
    url = f"http://api.scraperapi.com?api_key={scraper_key}&url={TARGET_URL}&render=true&country_code=it"
    resp = requests.get(url, timeout=90)
    resp.raise_for_status()
    return resp.text


def analyze(html):
    text = html.lower()
    log.info(f"Page size: {len(html)} bytes")

    for keyword in ["esaurito", "sold out", "acquista", "add to cart", "compra",
                    "aggiungi", "buy now", "disponib", "biglietti"]:
        idx = 0
        while True:
            idx = text.find(keyword, idx)
            if idx == -1:
                break
            snippet = text[max(0, idx-50):idx+80].replace("\n", " ").strip()
            log.info(f"  '{keyword}': ...{snippet}...")
            idx += len(keyword)

    is_avail = any(s in text for s in AVAIL_SIGNALS)
    is_hard_sold = any(s in text for s in SOLD_SIGNALS)

    log.info(f"  -> Buy button found: {is_avail} | Hard sold-out: {is_hard_sold}")

    if is_avail:
        return "available"
    elif is_hard_sold:
        return "sold_out"
    else:
        sample = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))[:600]
        log.info(f"Text sample: {sample}")
        return "unknown"


def check_availability():
    methods = [
        ("ZenRows", fetch_via_zenrows),
        ("ScraperAPI-rendered", fetch_via_scraperapi_rendered),
    ]
    for name, fn in methods:
        try:
            log.info(f"Trying {name}...")
            html = fn()
            if html and len(html) > 500:
                log.info(f"{name} succeeded ({len(html)} bytes)")
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
      <h2 style="color:#3B6D11;">Tickets are available!</h2>
      <p>The monitor detected availability on the Cenacolo Vinciano booking page. Act fast!</p>
      <p>
        <a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:12px 24px;
        border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;">Book now</a>
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
