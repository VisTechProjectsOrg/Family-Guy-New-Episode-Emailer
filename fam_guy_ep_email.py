import requests
from datetime import datetime, date, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from string import Template
import re
import os
import random
import glob
import logging
import argparse
from email.mime.image import MIMEImage

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LATEST_EP_FILE = os.path.join(SCRIPT_DIR, "latest_episode.json")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config", "config.json")
TEMPLATE_FILE = os.path.join(SCRIPT_DIR, "config", "email_template.html")
TEMPLATE_UPCOMING_FILE = os.path.join(SCRIPT_DIR, "config", "email_template_upcoming.html")
UPCOMING_NOTIFIED_FILE = os.path.join(SCRIPT_DIR, "upcoming_notified.json")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "config", "images")
LOG_FILE = os.path.join(SCRIPT_DIR, "app.log")

def setup_logging(verbose=False):
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO if verbose else logging.ERROR)
    fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', '%Y-%m-%d %H:%M:%S')

    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(fmt)
        log.addHandler(console_handler)

    return log

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def load_template():
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return Template(f.read())

def load_upcoming_template():
    with open(TEMPLATE_UPCOMING_FILE, "r", encoding="utf-8") as f:
        return Template(f.read())

def load_upcoming_notified():
    if os.path.exists(UPCOMING_NOTIFIED_FILE):
        with open(UPCOMING_NOTIFIED_FILE, "r") as f:
            return json.load(f)
    return []

def save_upcoming_notified(upcoming):
    ids = [ep["id"] for ep in upcoming[:5]]
    with open(UPCOMING_NOTIFIED_FILE, "w") as f:
        json.dump(ids, f)

def has_new_upcoming(upcoming, notified_ids):
    if not upcoming:
        return False
    current_ids = [ep["id"] for ep in upcoming[:5]]
    return current_ids != notified_ids

def pick_random_characters(count=2):
    files = sorted(glob.glob(os.path.join(IMAGES_DIR, "*.png")))
    if not files:
        return []
    return random.sample(files, min(count, len(files)))

def send_email(subject, body, email_config, log, inline_images=None):
    msg = MIMEMultipart("related")
    msg["From"] = email_config["username"]
    msg["To"] = ", ".join(email_config["to"])
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))

    for cid, path in (inline_images or {}).items():
        try:
            with open(path, "rb") as fh:
                img = MIMEImage(fh.read())
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=os.path.basename(path))
            msg.attach(img)
        except Exception as e:
            log.error(f"Failed to attach image {path}: {e}")

    try:
        with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"]) as server:
            server.starttls()
            server.login(email_config["username"], email_config["password"])
            server.sendmail(email_config["username"], email_config["to"], msg.as_string())
    except Exception as e:
        log.error(f"Failed to send email: {e}")
        return False
    return True

def load_previous_episode():
    if os.path.exists(LATEST_EP_FILE):
        with open(LATEST_EP_FILE, "r") as f:
            return json.load(f)
    return None

def save_latest_episode(episode):
    with open(LATEST_EP_FILE, "w") as f:
        json.dump({
            "title": episode["name"],
            "season": episode["season"],
            "episode": episode["number"],
            "airdate": episode["airdate"]
        }, f, indent=2)

def fetch_episodes(log):
    url = "https://api.tvmaze.com/singlesearch/shows?q=family+guy&embed=episodes"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        log.error(f"Failed to fetch episode data: {e}")
        return None, []

    today = date.today().isoformat()
    episodes = data.get("_embedded", {}).get("episodes", [])

    if not episodes:
        log.error("No episodes found in API response")
        return None, []

    # Use airdate < today so emails go out the day after Fox airing,
    # which is when episodes actually appear on Disney+.
    aired = [ep for ep in episodes if ep.get("airdate") and ep["airdate"] < today]
    upcoming = [ep for ep in episodes if ep.get("airdate") and ep["airdate"] >= today]

    if not aired:
        log.error("No aired episodes found")
        return None, upcoming

    latest = max(aired, key=lambda ep: (ep["airdate"], ep["season"], ep["number"]))
    summary = latest.get("summary") or "No summary available."
    latest["summary"] = re.sub(r'</?p>', '', summary).strip()
    return latest, upcoming


def format_upcoming_html(upcoming):
    if not upcoming:
        return ""

    cell = 'style="padding:8px 10px; font-size:13px; color:#3a2f24; border-bottom:1px solid #e8dfd0;"'
    rows = ""
    for i, ep in enumerate(upcoming[:5]):
        title = ep.get("name") or "TBA"
        airdate = ep.get("airdate") or "TBA"
        try:
            streaming = (datetime.strptime(airdate, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            streaming = "TBA"
        bg = "#ffffff" if i % 2 == 0 else "#fdf6ec"
        rows += (
            f'<tr bgcolor="{bg}">'
            f'<td {cell}><strong>S{ep["season"]}E{ep["number"]}</strong></td>'
            f'<td {cell}>{title}</td>'
            f'<td {cell}>{airdate}</td>'
            f'<td {cell}>{streaming}</td>'
            f'</tr>'
        )

    head = 'style="padding:8px 10px; text-align:left; font-size:11px; font-weight:bold; color:#ffffff; text-transform:uppercase; letter-spacing:0.5px;"'
    return f"""
      <p style="margin:0 0 12px 0; font-size:14px; font-weight:bold; color:#1a1a1a;">Upcoming Episodes</p>
      <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse; border:1px solid #c8102e;">
        <tr bgcolor="#c8102e">
          <th {head} width="60">Ep</th>
          <th {head}>Title</th>
          <th {head} width="80">Aired</th>
          <th {head} width="80">Disney+</th>
        </tr>
        {rows}
      </table>
    """

def is_new_episode(latest, previous):
    return not previous or previous["season"] != latest["season"] or previous["episode"] != latest["number"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='store_true', help='Print output to console')
    args = parser.parse_args()

    log = setup_logging(args.verbose)

    try:
        config = load_config()
        html_template = load_template()
        upcoming_template = load_upcoming_template()
    except Exception as e:
        log.error(f"Failed to load config/template: {e}")
        return

    email_config = config["email"]
    previous_episode = load_previous_episode()
    notified_upcoming = load_upcoming_notified()
    latest, upcoming = fetch_episodes(log)

    new_ep = latest and is_new_episode(latest, previous_episode)

    chars = pick_random_characters(2)
    inline_images = {}
    char1_html = ""
    char2_html = ""
    if len(chars) >= 1:
        inline_images["char1"] = chars[0]
        char1_html = '<img src="cid:char1" alt="" width="90" style="display:block; border:0; outline:none;">'
    if len(chars) >= 2:
        inline_images["char2"] = chars[1]
        char2_html = '<img src="cid:char2" alt="" width="90" style="display:block; border:0; outline:none;">'

    if new_ep:
        log.info(f"New episode: S{latest['season']}E{latest['number']} - {latest['name']}")
        subject = f"New Family Guy Episode: S{latest['season']}E{latest['number']}"
        upcoming_html = format_upcoming_html(upcoming)
        body = html_template.substitute(
            title=latest['name'],
            season=latest['season'],
            episode=latest['number'],
            airdate=latest['airdate'],
            summary=latest['summary'],
            upcoming=upcoming_html,
            character1=char1_html,
            character2=char2_html,
        )
        if send_email(subject, body, email_config, log, inline_images):
            save_latest_episode(latest)
            if upcoming:
                save_upcoming_notified(upcoming)
            log.info("New episode email sent")
    elif has_new_upcoming(upcoming, notified_upcoming):
        log.info(f"New upcoming episodes detected: {len(upcoming)} scheduled")
        subject = "Family Guy: Upcoming Episodes"
        upcoming_html = format_upcoming_html(upcoming)
        body = upcoming_template.substitute(
            upcoming=upcoming_html,
            character1=char1_html,
            character2=char2_html,
        )
        if send_email(subject, body, email_config, log, inline_images):
            save_upcoming_notified(upcoming)
            log.info("Upcoming episodes email sent")
    else:
        log.info("No new episode or upcoming changes")

if __name__ == "__main__":
    main()
