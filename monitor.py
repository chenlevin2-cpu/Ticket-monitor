import os
import re
import time
import logging
import requests
from datetime import datetime, date

# --- Configuration ---
TARGET_URL = "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547"
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "1800"))
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

last_available_slots = None


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


def parse_available_slots(html):
    """
    Find all available time slots by looking for links with title="N seats".
    Example: <a href="...&pcode=13771949&tcode=vt0005655" title="3 seats">12.30</a>
    Returns list of dicts with pcode, seats, time.
    """
    # Match: pcode=XXXXXXX...title="N seats">TIME
    pattern = r'pcode=(\d+)[^"]*"[^>]*title="(\d+)\s+seats?"[^>]*>([^<]+)</a>'
    matches = re.findall(pattern, html, re.IGNORECASE)

    slots = []
    for pcode, seats, time_str in matches:
        slots.append({
            "pcode": pcode,
            "seats": int(seats),
            "time": time_str.strip(),
        })

    return slots


def parse_sessions(html):
    """Parse the eventi JS array to map session IDs to dates."""
    pattern = r"eventi\['\d+'\]\.push\(new Array\s*\('([^']+)',\s*'([^']+)',\s*new Date\s*\((\d+),\s*\((\d+)-1\),\s*(\d+)\)"
    matches = re.findall(pattern, html)
    sessions = {}
    for m in matches:
        _, session_id, year, month, day = m
        try:
            sessions[session_id] = date(int(year), int(month), int(day))
        except Exception:
            pass
    return sessions


def check_availability():
    html = None
    for name, fn in [("ZenRows", fetch_with_zenrows), ("ScraperAPI", fetch_with_scraperapi)]:
        try:
            log.info(f"Trying {name}...")
            html = fn()
            if html and len(html) > 500:
                log.info(f"{name} succeeded ({len(html)} bytes)")
                break
            html = None
        except Exception as e:
            log.warning(f"{name} failed: {e}")

    if not html:
        log.error("All fetch methods failed")
        return "error", []

    # Find available slots (links with "N seats" title)
    slots = parse_available_slots(html)
    log.info(f"Available time slots found: {len(slots)}")
    for s in slots:
        log.info(f"  pcode={s['pcode']} time={s['time']} seats={s['seats']}")

    if slots:
        return "available", slots
    else:
        log.info("No available slots found ג€” all sold out")
        return "sold_out", []


def send_email_alert(available_slots):
    if not SENDGRID_API_KEY:
        log.error("SENDGRID_API_KEY not set ג€” sign up free at sendgrid.com")
        return
    if not RECIPIENT_EMAIL or not SENDER_EMAIL:
        log.error("RECIPIENT_EMAIL or SENDER_EMAIL not set")
        return

    rows = "".join(
        f"<tr>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #eee'>{s['time']}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #eee;text-align:center;color:#3B6D11;font-weight:bold'>{s['seats']} seats</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #eee'>"
        f"<a href='https://cenacolovinciano.vivaticket.it/index.php?nvpg[sell]&cmd=prices&wms_op=cenacoloVinciano&pcode={s[\"pcode\"]}&tcode=vt0005655' style='color:#185FA5'>Book this slot</a>"
        f"</td></tr>"
        for s in available_slots
    )

    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:580px;margin:auto;padding:24px;">
      <h2 style="color:#3B6D11;">נ¨ Last Supper tickets available!</h2>
      <p>The following time slots are bookable right now ג€” act fast!</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead><tr style="background:#f5f5f5">
          <th style="padding:8px 12px;text-align:left">Time</th>
          <th style="padding:8px 12px;text-align:center">Seats</th>
          <th style="padding:8px 12px;text-align:left">Link</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p><a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:12px 24px;
        border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;">Open booking page ג†’</a></p>
      <p style="color:#888;font-size:12px">Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    </body></html>
    """

    payload = {
        "personalizations": [{"to": [{"email": RECIPIENT_EMAIL}]}],
        "from": {"email": SENDER_EMAIL},
        "subject": f"נ¨ Last Supper ג€” {len(available_slots)} slot(s) available now!",
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
            log.info(f"ג… Alert email sent to {RECIPIENT_EMAIL}")
        else:
            log.error(f"SendGrid error {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def main():
    global last_available_slots
    log.info("=== Cenacolo Vinciano Ticket Monitor Started ===")
    log.info(f"Notifying: {RECIPIENT_EMAIL}")
    log.info(f"Check interval: {CHECK_INTERVAL}s")

    while True:
        log.info("Checking availability...")
        status, available_slots = check_availability()
        log.info(f"Status: {status}")

        if status == "available":
            current_pcodes = set(s["pcode"] for s in available_slots)
            if last_available_slots is None or not current_pcodes.issubset(last_available_slots):
                log.info("New slots detected ג€” sending alert!")
                send_email_alert(available_slots)
            last_available_slots = current_pcodes
        else:
            last_available_slots = set()

        log.info(f"Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
