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
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

last_status = None


def fetch_with_zenrows():
    params = {
        "apikey": ZENROWS_API_KEY,
        "url": TARGET_URL,
        "js_render": "true",
        "wait": "5000",
        "premium_proxy": "true",
    }
    resp = requests.get("https://api.zenrows.com/v1/", params=params, timeout=90)
    resp.raise_for_status()
    return resp.text


def fetch_with_scraperapi():
    if not SCRAPER_API_KEY:
        raise Exception("No SCRAPER_API_KEY")
    url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={TARGET_URL}&render=true&country_code=it"
    resp = requests.get(url, timeout=90)
    resp.raise_for_status()
    return resp.text


def analyze_html(html):
    log.info(f"Page size: {len(html)} bytes")

    # Strip tags and get clean text
    clean = re.sub(r'<[^>]+>', ' ', html)
    clean = re.sub(r'\s+', ' ', clean).strip()

    # Log a large chunk of the visible text so we can see what buttons/text exist
    log.info(f"=== PAGE TEXT (first 1500 chars) ===")
    log.info(clean[:750])
    log.info(clean[750:1500])
    log.info(f"=== END PAGE TEXT ===")

    # Also search for the sell_online section specifically
    sell_idx = html.lower().find('sell_online')
    if sell_idx != -1:
        section = html[sell_idx:sell_idx+2000]
        section_clean = re.sub(r'<[^>]+>', ' ', section)
        section_clean = re.sub(r'\s+', ' ', section_clean).strip()
        log.info(f"=== sell_online SECTION ===")
        log.info(section_clean[:800])
        log.info(f"=== END sell_online ===")

    # Search for any input/button/link text
    buttons = re.findall(r'<(?:button|input|a)[^>]*>([^<]{2,60})</(?:button|a)>', html, re.IGNORECASE)
    if buttons:
        log.info(f"Buttons/links found: {buttons[:20]}")

    text = html.lower()

    # Hard sold-out signals
    hard_sold = ["esaurito", "biglietti esauriti", "nessuna data disponibile",
                 "no dates available", "evento non disponibile"]
    # Any of these in the sell section = available
    avail_signals = ["aggiungi al carrello", "add to cart", "compra ora",
                     "buy now", "acquista ora", "select tickets",
                     '"available":true', '"available": true',
                     '"soldout":false', '"sold_out":false',
                     'type="submit"', "proceed to checkout", "checkout"]

    is_avail = any(s in text for s in avail_signals)
    is_sold = any(s in text for s in hard_sold)

    log.info(f"Available signals: {is_avail} | Sold-out signals: {is_sold}")

    if is_avail and not is_sold:
        return "available"
    elif is_sold and not is_avail:
        return "sold_out"
    elif is_avail and is_sold:
        return "available"  # some dates available, some not
    return "unknown"


def check_availability():
    for name, fn in [("ZenRows", fetch_with_zenrows), ("ScraperAPI", fetch_with_scraperapi)]:
        try:
            log.info(f"Trying {name}...")
            html = fn()
            if html and len(html) > 500:
                log.info(f"{name} succeeded ({len(html)} bytes)")
                return analyze_html(html)
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
