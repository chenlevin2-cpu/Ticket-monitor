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
# Vivaticket API endpoint for event sessions/dates
API_URL = "https://www.vivaticket.com/it/api/event/sessions?eventId=151991&lang=en"
API_URL2 = "https://cenacolovinciano.vivaticket.it/it/api/event/sessions?eventId=151991"

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Referer": "https://cenacolovinciano.vivaticket.it/",
    "Origin": "https://cenacolovinciano.vivaticket.it",
}


def check_via_api():
    """Try vivaticket's own API endpoints for session availability."""
    for url in [API_URL, API_URL2]:
        try:
            log.info(f"Trying API: {url}")
            resp = requests.get(url, headers=HEADERS, timeout=20)
            log.info(f"API status: {resp.status_code}, size: {len(resp.text)} bytes")
            log.info(f"API response: {resp.text[:500]}")
            if resp.status_code == 200 and len(resp.text) > 10:
                return resp.text
        except Exception as e:
            log.warning(f"API call failed: {e}")
    return None


def check_via_page_js(html):
    """Extract availability from JavaScript variables embedded in the page."""
    # Look for JS patterns that indicate session/slot data
    patterns = [
        r'sessions?\s*[=:]\s*(\[.*?\])',
        r'dates?\s*[=:]\s*(\[.*?\])',
        r'slots?\s*[=:]\s*(\[.*?\])',
        r'"available"\s*:\s*(true|false)',
        r'"availability"\s*:\s*(\d+)',
        r'availab\w+\s*[=:]\s*(\w+)',
        r'"sold_?out"\s*:\s*(true|false)',
        r'soldOut\s*[=:]\s*(true|false)',
        r'remaining\s*[=:]\s*(\d+)',
        r'"qty"\s*:\s*(\d+)',
        r'"quantity"\s*:\s*(\d+)',
    ]

    found_any = False
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
        if matches:
            found_any = True
            log.info(f"  JS pattern '{pattern}': {matches[:3]}")

    return found_any


def analyze_html(html):
    """Full analysis of page HTML/JS for availability signals."""
    text = html.lower()
    log.info(f"Page size: {len(html)} bytes")

    # Look for JS-embedded availability data
    check_via_page_js(html)

    # Log all keyword occurrences with context
    for keyword in ["esaurito", "sold", "acquista", "add to cart", "compra",
                    "aggiungi", "buy now", "disponib", "available", "unavailable",
                    "session", "slot", "remaining", "qty", "quantity"]:
        idx = text.find(keyword)
        if idx != -1:
            snippet = text[max(0, idx-40):idx+70].replace("\n", " ").strip()
            log.info(f"  '{keyword}': ...{snippet}...")

    # Hard sold-out: whole event marked unavailable
    hard_sold = ["esaurito", "biglietti esauriti", "nessuna data disponibile",
                 "no dates available", "evento non disponibile"]
    # Definitive available: action buttons or JS true availability
    definitive_avail = ["aggiungi al carrello", "add to cart", "compra ora",
                        "buy now", "acquista ora", '"available":true', '"available": true',
                        'available:true', '"soldout":false', '"sold_out":false']

    is_avail = any(s in text for s in definitive_avail)
    is_sold = any(s in text for s in hard_sold)

    log.info(f"  -> Available signals: {is_avail} | Sold-out signals: {is_sold}")

    if is_avail:
        return "available"
    elif is_sold:
        return "sold_out"
    return "unknown"


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


def check_availability():
    # Step 1: Try the API directly (cheapest, no scraping credits used)
    api_data = check_via_api()
    if api_data:
        data_lower = api_data.lower()
        # Check for available slots in API response
        if any(x in data_lower for x in ['"available":true', '"available": true',
                                           '"soldout":false', '"qty":', '"remaining":']):
            # Parse more carefully
            avail_count = data_lower.count('"available":true') + data_lower.count('"available": true')
            sold_count = data_lower.count('"available":false') + data_lower.count('"available": false')
            log.info(f"API: {avail_count} available slots, {sold_count} unavailable slots")
            if avail_count > 0:
                return "available"
            elif sold_count > 0 and avail_count == 0:
                return "sold_out"

    # Step 2: Fetch the full page with JS rendering
    for name, fn in [("ZenRows", fetch_with_zenrows), ("ScraperAPI", fetch_with_scraperapi)]:
        try:
            log.info(f"Trying {name}...")
            html = fn()
            if html and len(html) > 500:
                log.info(f"{name} succeeded ({len(html)} bytes)")
                return analyze_html(html)
            log.warning(f"{name} too small")
        except Exception as e:
            log.warning(f"{name} failed: {e}")

    log.error("All methods failed")
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
