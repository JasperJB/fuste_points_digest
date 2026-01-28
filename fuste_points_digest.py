import os
import json
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime
import requests
from bs4 import BeautifulSoup

NEWS_URL = "https://thefuste.com/news"
STATE_PATH = os.environ.get("STATE_PATH", "state.json")

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def fetch_latest_news():
    r = requests.get(NEWS_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Squarespace blogs typically render articles with <article> tags.
    article = soup.find("article")
    if not article:
        raise RuntimeError("Could not find an <article> on /news; site layout may have changed.")

    # Title + date
    title_el = article.find(["h1", "h2"])
    title = title_el.get_text(" ", strip=True) if title_el else "Untitled"

    # Squarespace dates often appear in time tags or adjacent metadata blocks.
    date_text = None
    time_el = article.find("time")
    if time_el:
        date_text = time_el.get_text(" ", strip=True)
    if not date_text:
        # fallback: look for something that parses like a date
        meta_text = article.get_text("\n", strip=True).splitlines()[:30]
        for line in meta_text:
            if any(m in line for m in ["January", "February", "March", "April", "May", "June",
                                       "July", "August", "September", "October", "November", "December"]):
                date_text = line.strip()
                break
    date_text = date_text or "Unknown date"

    # Extract bullet lists under headings "Bonuses" and "Side Quests"
    def extract_list_after_heading(heading_text):
        # Find a tag whose text matches heading_text (case-insensitive), then the next <ul>
        for h in article.find_all(["h2", "h3", "strong"]):
            if h.get_text(" ", strip=True).lower() == heading_text.lower():
                nxt = h
                for _ in range(25):
                    nxt = nxt.find_next()
                    if not nxt:
                        break
                    if nxt.name == "ul":
                        return [li.get_text(" ", strip=True) for li in nxt.find_all("li")]
        return []

    bonuses = extract_list_after_heading("Bonuses")
    side_quests = extract_list_after_heading("Side Quests")

    # If headings aren’t structured, do a looser heuristic: find lines after "Bonuses" / "Side Quests"
    if not bonuses or not side_quests:
        text = article.get_text("\n", strip=True).splitlines()
        def grab_lines(after_label, stop_labels):
            out, on = [], False
            for line in text:
                l = line.strip()
                if l.lower().startswith(after_label.lower()):
                    on = True
                    continue
                if on and any(l.lower().startswith(s.lower()) for s in stop_labels):
                    break
                if on and l and not l.lower().startswith(after_label.lower()):
                    # keep short-ish lines that look like list items
                    if len(l) <= 120:
                        out.append(l)
            return out

        if not bonuses:
            bonuses = grab_lines("Bonuses", ["Side Quests", "That’s it", "Cheers", "GG"])
        if not side_quests:
            side_quests = grab_lines("Side Quests", ["That’s it", "Cheers", "GG"])

        # de-noise: keep distinctive items
        bonuses = [b for b in bonuses if b.lower() not in ("bonuses",)]
        side_quests = [s for s in side_quests if s.lower() not in ("side quests",)]

    post_key = f"{date_text}|{title}"

    return {
        "post_key": post_key,
        "title": title,
        "date": date_text,
        "bonuses": bonuses,
        "side_quests": side_quests,
        "url": NEWS_URL,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }

def send_email(subject, body):
    to_email = os.environ["TO_EMAIL"]
    from_email = os.environ["FROM_EMAIL"]
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls(context=context)
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

def main():
    state = load_state()
    latest = fetch_latest_news()

    last_key = state.get("last_post_key")
    if last_key == latest["post_key"]:
        # No new weekly update post; do nothing (or optionally email "no change").
        print("No new post; skipping email.")
        return

    lines = []
    lines.append(f"THE FÜSTE weekly points update")
    lines.append(f"Post: {latest['title']}")
    lines.append(f"Date: {latest['date']}")
    lines.append(f"Link: {latest['url']}")
    lines.append("")
    lines.append("Bonuses:")
    if latest["bonuses"]:
        for b in latest["bonuses"]:
            lines.append(f"  - {b}")
    else:
        lines.append("  (none detected)")
    lines.append("")
    lines.append("Side Quests:")
    if latest["side_quests"]:
        for s in latest["side_quests"]:
            lines.append(f"  - {s}")
    else:
        lines.append("  (none detected)")

    body = "\n".join(lines)
    subject = f"FÜSTE points update: {latest['title']} ({latest['date']})"
    send_email(subject, body)

    state["last_post_key"] = latest["post_key"]
    save_state(state)
    print("Email sent and state updated.")

if __name__ == "__main__":
    main()
