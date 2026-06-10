import os
import re
import time
import logging
import requests
from datetime import datetime, date

# --- Configuration ---
TARGET_URL = "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547"
BASE_URL = "https://cenacolovinciano.vivaticket.it"
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "1800"))
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

last_available_sessions = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Referer": "https://cenacolovinciano.vivaticket.it/",
}


def fetch_main_page():
    """Fetch main page via ZenRows to get the eventi array with session IDs."""
    params = {
        "apikey": ZENROWS_API_KEY,
        "url": TARGET_URL,
        "js_render": "true",
        "wait": "3000",
        "premium_proxy": "true",
    }
    resp = requests.get("https://api.zenrows.com/v1/", params=params, timeout=90)
    resp.raise_for_status()
    return resp.text


def parse_sessions(html):
    """Parse eventi JS array to get all session IDs and dates."""
    pattern = r"eventi\['\d+'\]\.push\(new Array\s*\('([^']+)',\s*'([^']+)',\s*new Date\s*\((\d+),\s*\((\d+)-1\),\s*(\d+)\)"
    matches = re.findall(pattern, html)
    sessions = []
    for shop_id, session_id, year, month, day in matches:
        try:
            sessions.append({
                "session_id": session_id,
                "shop_id": shop_id,
                "date": date(int(year), int(month), int(day)),
            })
        except Exception:
            pass
    return sessions


def check_session_availability(session):
    """
    Check a specific session by fetching its ticket selection page directly.
    URL: /index.php?nvpg[sell]&cmd=calendar_detail&show_id=151991&session_id=XXXXX&tcode=vt0005655
    """
    url = (
        f"{BASE_URL}/index.php?nvpg[sell]&cmd=calendar_detail"
        f"&show_id=151991&session_id={session['session_id']}&tcode={session['shop_id']}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        html = resp.text.lower()

        # Available if we see seat/ticket purchase options
        avail_signals = ["add to cart", "aggiungi", "acquista", "seats available",
                         "posti disponibili", "select", "buy", "compra", "title="]
        sold_signals = ["sold out", "esaurito", "no availability", "not available"]

        is_avail = any(s in html for s in avail_signals)
        is_sold = any(s in html for s in sold_signals)

        # Also look for seat count pattern
        seat_match = re.search(r'(\d+)\s+seat[s]?\s+available', html)
        if seat_match:
            return True, int(seat_match.group(1))

        if is_avail and not is_sold:
            return True, None
        return False, None
    except Exception as e:
        log.warning(f"Failed to check session {session['session_id']}: {e}")
        return False, None


def check_availability():
    # Step 1: Get main page to extract all sessions
    try:
        log.info("Fetching main page for session list...")
        html = fetch_main_page()
        log.info(f"Main page: {len(html)} bytes")
    except Exception as e:
        log.error(f"Failed to fetch main page: {e}")
        return "error", []

    sessions = parse_sessions(html)
    if not sessions:
        log.warning("No sessions found in page")
        return "unknown", []

    log.info(f"Found {len(sessions)} sessions, checking each for availability...")

    # Step 2: Check only the next 14 days to keep it fast
    today = date.today()
    upcoming = [s for s in sessions if (s["date"] - today).days <= 30]
    log.info(f"Checking {len(upcoming)} sessions in next 30 days...")

    available = []
    for s in upcoming:
        is_avail, seats = check_session_availability(s)
        label = f"{seats} seats" if seats else "available"
        log.info(f"  {s['date'].strftime('%d %b %Y')} ({s['session_id']}): {'AVAILABLE - ' + label if is_avail else 'sold out'}")
        if is_avail:
            available.append({**s, "seats": seats})
        time.sleep(0.5)  # be gentle

    if available:
        return "available", available
    return "sold_out", []


def send_email_alert(available_sessions):
    if not SENDGRID_API_KEY:
        log.error("SENDGRID_API_KEY not set")
        return

    rows = ""
    for s in available_sessions:
        seats_str = f"{s['seats']} seats" if s.get("seats") else "available"
        rows += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee'>{s['date'].strftime('%A, %d %B %Y')}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;text-align:center;color:#3B6D11;font-weight:bold'>{seats_str}</td>"
            f"</tr>"
        )

    html_body = (
        "<html><body style='font-family:sans-serif;max-width:580px;margin:auto;padding:24px'>"
        "<h2 style='color:#3B6D11'>Last Supper tickets available!</h2>"
        "<p>The following dates have availability — act fast!</p>"
        "<table style='width:100%;border-collapse:collapse;margin:16px 0'>"
        "<thead><tr style='background:#f5f5f5'>"
        "<th style='padding:8px 12px;text-align:left'>Date</th>"
        "<th style='padding:8px 12px;text-align:center'>Availability</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p><a href='{TARGET_URL}' style='background:#185FA5;color:#fff;padding:12px 24px;"
        "border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px'>Book now</a></p>"
        f"<p style='color:#888;font-size:12px'>Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
        "</body></html>"
    )

    payload = {
        "personalizations": [{"to": [{"email": RECIPIENT_EMAIL}]}],
        "from": {"email": SENDER_EMAIL},
        "subject": f"Last Supper — {len(available_sessions)} date(s) available!",
        "content": [{"type": "text/html", "value": html_body}]
    }

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {SENDGRID_API_KEY}"},
            timeout=30
        )
        if resp.status_code == 202:
            log.info(f"Alert email sent to {RECIPIENT_EMAIL}")
        else:
            log.error(f"SendGrid error {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def main():
    global last_available_sessions
    log.info("=== Cenacolo Vinciano Ticket Monitor Started ===")
    log.info(f"Notifying: {RECIPIENT_EMAIL}")
    log.info(f"Check interval: {CHECK_INTERVAL}s")

    while True:
        log.info("Checking availability...")
        status, available_sessions = check_availability()
        log.info(f"Status: {status}")

        if status == "available":
            current_ids = set(s["session_id"] for s in available_sessions)
            if last_available_sessions is None or not current_ids.issubset(last_available_sessions):
                log.info("New availability — sending alert!")
                send_email_alert(available_sessions)
            last_available_sessions = current_ids
        else:
            last_available_sessions = set()

        log.info(f"Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
