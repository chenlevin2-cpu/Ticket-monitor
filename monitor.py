import os
import re
import time
import logging
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date

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

last_available_dates = None


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


def parse_sessions(html):
    """
    Parse the eventi JS array from the page.
    Format: eventi['151991'].push(new Array('shopId','sessionId', new Date(year, month-1, day), '0', slots, '0'))
    Returns list of dicts with date and slots available.
    """
    pattern = r"eventi\['\d+'\]\.push\(new Array\s*\('([^']+)',\s*'([^']+)',\s*new Date\s*\((\d+),\s*\((\d+)-1\),\s*(\d+)\),\s*'(\d+)',\s*(\d+),\s*'(\d+)'\s*\)\s*\)"
    matches = re.findall(pattern, html)

    sessions = []
    for m in matches:
        shop_id, session_id, year, month, day, unknown1, slots, unknown2 = m
        try:
            session_date = date(int(year), int(month), int(day))
            slots_available = int(slots)
            sessions.append({
                "date": session_date,
                "session_id": session_id,
                "slots": slots_available,
            })
        except Exception as e:
            log.warning(f"Could not parse session: {m} — {e}")

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
            log.warning(f"{name} too small")
            html = None
        except Exception as e:
            log.warning(f"{name} failed: {e}")

    if not html:
        log.error("All fetch methods failed")
        return "error", []

    sessions = parse_sessions(html)
    if not sessions:
        log.warning("No session data found in page — structure may have changed")
        return "unknown", []

    available = [s for s in sessions if s["slots"] > 0]
    sold_out = [s for s in sessions if s["slots"] == 0]

    log.info(f"Sessions found: {len(sessions)} total, {len(available)} available, {len(sold_out)} sold out")
    for s in sessions:
        status = f"{s['slots']} slots" if s['slots'] > 0 else "SOLD OUT"
        log.info(f"  {s['date'].strftime('%d %b %Y')} ({s['session_id']}): {status}")

    if available:
        return "available", available
    else:
        return "sold_out", []


def send_email_alert(available_sessions):
    log.info(f"Attempting to send email from {SENDER_EMAIL} to {RECIPIENT_EMAIL}")

    if not SENDER_EMAIL:
        log.error("SENDER_EMAIL is not set")
        return
    if not SENDER_PASSWORD:
        log.error("SENDER_PASSWORD is not set")
        return
    if not RECIPIENT_EMAIL:
        log.error("RECIPIENT_EMAIL is not set")
        return

    # Build the dates table
    rows = ""
    for s in available_sessions:
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;">{s['date'].strftime('%A, %d %B %Y')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;color:#3B6D11;font-weight:bold;">{s['slots']} slots</td>
        </tr>"""

    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:560px;margin:auto;padding:24px;">
      <h2 style="color:#3B6D11;">🎨 Last Supper tickets available!</h2>
      <p>The following dates have availability right now:</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0;">
        <thead>
          <tr style="background:#f5f5f5;">
            <th style="padding:8px 12px;text-align:left;">Date</th>
            <th style="padding:8px 12px;text-align:center;">Slots</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p>
        <a href="{TARGET_URL}" style="background:#185FA5;color:#fff;padding:12px 24px;
        border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;">Book now →</a>
      </p>
      <p style="color:#888;font-size:12px;">Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎨 Last Supper — {len(available_sessions)} date(s) available!"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        log.info("Connecting to Gmail SMTP...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            log.info("Connected. Logging in...")
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            log.info("Logged in. Sending...")
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        log.info(f"✅ Alert email sent successfully to {RECIPIENT_EMAIL}")
    except smtplib.SMTPAuthenticationError as e:
        log.error(f"Gmail authentication failed — check SENDER_PASSWORD is an App Password, not your login password. Error: {e}")
    except smtplib.SMTPException as e:
        log.error(f"SMTP error: {e}")
    except Exception as e:
        log.error(f"Failed to send email: {type(e).__name__}: {e}")


def main():
    global last_available_dates
    log.info("=== Cenacolo Vinciano Ticket Monitor Started ===")
    log.info(f"Notifying: {RECIPIENT_EMAIL}")
    log.info(f"Check interval: {CHECK_INTERVAL}s")

    while True:
        log.info("Checking availability...")
        status, available_sessions = check_availability()
        log.info(f"Status: {status}")

        if status == "available":
            # Get set of currently available date strings
            current_dates = set(s["date"].isoformat() for s in available_sessions)

            # Alert if this is the first check, or if new dates appeared
            if last_available_dates is None or not current_dates.issubset(last_available_dates):
                log.info("New availability detected — sending alert!")
                send_email_alert(available_sessions)

            last_available_dates = current_dates
        else:
            last_available_dates = set()

        log.info(f"Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
