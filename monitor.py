import os
import re
import time
import logging
import requests
from datetime import datetime, date

# --- Configuration ---
TARGET_URL = "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547"
API_URL = "https://cenacolovinciano.vivaticket.it/eventoWidgetTlite.php"
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "1800"))
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

last_available_sessions = None

# Target sessions for Aug 18 and 19
# session_id = from eventi array; pcodes = time slot IDs within the session
# pcodes are unknown until dates become available — we detect via session-level API
TARGET_SESSIONS = [
    {"session_id": "13792815", "shop_id": "vt0005655", "date": date(2026, 8, 18)},
    {"session_id": "13792822", "shop_id": "vt0005655", "date": date(2026, 8, 19)},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Referer": TARGET_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://cenacolovinciano.vivaticket.it",
}


def check_session_via_page(session):
    """
    Fetch the calendar detail page for a specific session.
    This is the page that loads when you click a date on the calendar.
    Look for available time slots (green buttons with pcode links).
    """
    # This URL loads the timetable for a specific session
    url = (
        f"https://cenacolovinciano.vivaticket.it/index.php"
        f"?nvpg[sell]&cmd=calendar_detail&show_id=151991"
        f"&session_id={session['session_id']}&tcode={session['shop_id']}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        html = resp.text
        log.info(f"  Session page {session['date']}: {len(html)} bytes, status {resp.status_code}")

        # Log a text sample
        clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)).strip()
        log.info(f"  Text sample: {clean[:400]}")

        # Look for available slot indicators
        # Available slots have links with pcode, unavailable don't
        pcodes = re.findall(r'pcode=(\d+)', html)
        log.info(f"  pcodes found: {pcodes[:10]}")

        avail = re.findall(r'title="(\d+)\s+seat', html, re.IGNORECASE)
        log.info(f"  seat titles found: {avail}")

        if pcodes or avail:
            return True, pcodes
        return False, []
    except Exception as e:
        log.warning(f"  Page fetch failed for {session['date']}: {e}")
        return False, []


def check_session_via_api(session):
    """
    Try the eventoWidgetTlite API with session_id directly.
    Log full response for diagnosis.
    """
    # Try multiple pcode formats
    attempts = [
        {"ajax": "1", "cal": "1", "tcode": session["shop_id"], "pcode": session["session_id"], "seat-filter": "undefined"},
        {"ajax": "1", "cal": "1", "tcode": session["shop_id"], "show_id": "151991", "session_id": session["session_id"]},
        {"ajax": "1", "tcode": session["shop_id"], "pcode": session["session_id"]},
    ]
    for i, data in enumerate(attempts):
        try:
            resp = requests.post(API_URL, data=data, headers=HEADERS, timeout=15)
            log.info(f"  API attempt {i+1}: status={resp.status_code} body={resp.text[:300]}")
            if resp.status_code == 200 and resp.text.strip().startswith("["):
                slots = resp.json()
                available = [s for s in slots if str(s.get("d", "0")) == "1"]
                if available:
                    return True, available
                return False, []
        except Exception as e:
            log.warning(f"  API attempt {i+1} failed: {e}")
    return False, []


def check_availability():
    log.info("Checking Aug 18 & 19...")
    available_sessions = []

    for s in TARGET_SESSIONS:
        log.info(f"Checking {s['date'].strftime('%d %b %Y')} (session {s['session_id']})...")

        # Try page-based check first
        is_avail, pcodes = check_session_via_page(s)

        # Also try API
        is_avail_api, slots = check_session_via_api(s)

        if is_avail or is_avail_api:
            log.info(f"  *** AVAILABLE! pcodes={pcodes} api_slots={slots}")
            available_sessions.append({**s, "slots": slots or pcodes})
        else:
            log.info(f"  Sold out")
        time.sleep(0.5)

    if available_sessions:
        return "available", available_sessions
    return "sold_out", []


def send_email_alert(available_sessions):
    if not SENDGRID_API_KEY:
        log.error("SENDGRID_API_KEY not set")
        return

    rows = ""
    for s in available_sessions:
        rows += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee'>{s['date'].strftime('%A, %d %B %Y')}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;color:#3B6D11;font-weight:bold'>Available!</td>"
            f"</tr>"
        )

    html_body = (
        "<html><body style='font-family:sans-serif;max-width:580px;margin:auto;padding:24px'>"
        "<h2 style='color:#3B6D11'>🎨 Last Supper tickets available!</h2>"
        "<p>Availability detected for your target dates — act fast!</p>"
        "<table style='width:100%;border-collapse:collapse;margin:16px 0'>"
        "<thead><tr style='background:#f5f5f5'>"
        "<th style='padding:8px 12px;text-align:left'>Date</th>"
        "<th style='padding:8px 12px;text-align:left'>Status</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p><a href='{TARGET_URL}' style='background:#185FA5;color:#fff;padding:12px 24px;"
        "border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px'>Book now →</a></p>"
        f"<p style='color:#888;font-size:12px'>Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
        "</body></html>"
    )

    payload = {
        "personalizations": [{"to": [{"email": RECIPIENT_EMAIL}]}],
        "from": {"email": SENDER_EMAIL},
        "subject": f"🎨 Last Supper — {len(available_sessions)} date(s) available!",
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
            log.info(f"✅ Alert email sent to {RECIPIENT_EMAIL}")
        else:
            log.error(f"SendGrid error {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def main():
    global last_available_sessions
    log.info("=== Cenacolo Vinciano Ticket Monitor Started ===")
    log.info(f"Notifying: {RECIPIENT_EMAIL}")
    log.info(f"Watching: Aug 18 & Aug 19 2026")
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
