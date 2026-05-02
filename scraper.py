import os
import json
import time
import requests
import gspread
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
APIFY_API_KEY      = os.environ["APIFY_API_KEY"]
GOOGLE_SHEET_ID    = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDENTIALS = os.environ["GOOGLE_CREDENTIALS"]
SHEET_TAB_NAME     = "Social_Leads"
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")

HOURS_FRESH        = 48
MAX_POSTS          = 30
MIN_GEMINI_SCORE   = 70   # only write leads scoring 70+

BASE_URL  = "https://api.apify.com/v2"
ACTOR_ID  = "trudax~reddit-scraper-lite"

# ─────────────────────────────────────────────
# SUBREDDITS ONLY — no keyword searches
# ─────────────────────────────────────────────
YOGA_SUBREDDITS = [
    "yoga",
    "xxfitness",
    "onlinefitness",
    "mommit",
    "beyondthebump",
    "fitness",
]

PIANO_SUBREDDITS = [
    "pianolessons",
    "piano",
    "learnpiano",
    "Parenting",
    "homeschool",
    "kidsactivities",
]

YOGA_INTENT_KEYWORDS = [
    "yoga class", "yoga teacher", "yoga instructor", "yoga lessons",
    "yoga online", "online yoga", "yoga recommendation", "yoga app",
    "learn yoga", "yoga beginner", "yoga practice", "yoga schedule",
    "yoga accountability", "yoga studio", "yoga routine",
]

PIANO_INTENT_KEYWORDS = [
    "piano lesson", "piano teacher", "piano class", "piano online",
    "online piano", "piano recommendation", "learn piano", "piano app",
    "piano instructor", "piano for kids", "music lesson", "music teacher",
    "piano practice", "piano beginner", "music school",
]

# ─────────────────────────────────────────────
# GOOGLE SHEET
# ─────────────────────────────────────────────
SHEET_HEADERS = [
    "#", "Date Found", "Time (IST)", "Post Age", "Platform",
    "Subreddit", "Author", "Post Title", "Post Snippet",
    "Post Link", "Business", "Gemini Score", "Status",
    "Outreach Method", "Outreach Date", "Response", "Outcome / Notes",
]

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        worksheet = spreadsheet.worksheet(SHEET_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=SHEET_TAB_NAME, rows=5000, cols=len(SHEET_HEADERS)
        )
        worksheet.append_row(SHEET_HEADERS)
        worksheet.format("A1:Q1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.17, "green": 0.24, "blue": 0.48},
            "horizontalAlignment": "CENTER",
        })
        print(f"  ✓ Created new tab: {SHEET_TAB_NAME}")
    return worksheet


def get_existing_links(worksheet):
    try:
        col_index = SHEET_HEADERS.index("Post Link") + 1
        all_values = worksheet.col_values(col_index)
        return set(v.strip() for v in all_values[1:] if v.strip())
    except Exception as e:
        print(f"  ⚠ Could not fetch existing links: {e}")
        return set()


def get_next_number(worksheet):
    try:
        col_values = worksheet.col_values(1)
        numbers = []
        for v in col_values[1:]:
            try:
                numbers.append(int(float(str(v))))
            except Exception:
                pass
        return max(numbers) + 1 if numbers else 1
    except Exception:
        return 1


def write_leads(worksheet, leads, existing_links, start_number):
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = datetime.now(timezone.utc) + ist_offset
    rows = []
    counter = start_number

    for lead in leads:
        url = lead["url"].strip()
        if url in existing_links:
            continue
        business = "🧘 Yoga" if lead["type"] == "yoga" else "🎹 Piano"
        row = [
            counter,
            now_ist.strftime("%d-%b-%Y"),
            now_ist.strftime("%I:%M %p"),
            lead["age"],
            "Reddit",
            lead["subreddit"],
            lead["author"],
            lead["title"][:150],
            lead["snippet"][:300],
            url,
            business,
            lead["score"],
            "New",
            "", "", "", "",
        ]
        rows.append(row)
        existing_links.add(url)
        counter += 1

    if rows:
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"  ✓ {len(rows)} new lead(s) written to sheet")
    else:
        print("  ℹ No new qualified leads to write")
    return len(rows)


# ─────────────────────────────────────────────
# GEMINI SCORING
# ─────────────────────────────────────────────
def score_with_gemini(title, snippet, post_type):
    """Score a post 0-100 for yoga or piano class buying intent."""
    if not GEMINI_API_KEY:
        # No Gemini key — use simple keyword scoring instead
        return keyword_score(title, snippet, post_type)

    prompt = f"""You are a lead qualification expert for an online {post_type} class school.
Score this Reddit post from 0-100 based on how likely the person is looking for an online {post_type} class.

100 = Explicitly asking for a {post_type} class/teacher online
80  = Strongly interested, asking for recommendations
60  = Curious about {post_type} online, exploring options
40  = Mentions {post_type} but not looking for a class
20  = Barely related
0   = Completely unrelated

Post Title: {title}
Post Content: {snippet[:500]}

Reply with ONLY a number between 0 and 100. Nothing else."""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            score = int(''.join(filter(str.isdigit, text))[:3])
            return min(100, max(0, score))
    except Exception:
        pass
    return keyword_score(title, snippet, post_type)


def keyword_score(title, snippet, post_type):
    """Fallback keyword-based scoring when Gemini is not available."""
    keywords = YOGA_INTENT_KEYWORDS if post_type == "yoga" else PIANO_INTENT_KEYWORDS
    combined = (title + " " + snippet).lower()
    matched = sum(1 for kw in keywords if kw.lower() in combined)
    if matched >= 3:
        return 85
    elif matched == 2:
        return 72
    elif matched == 1:
        return 55
    return 20


# ─────────────────────────────────────────────
# APIFY
# ─────────────────────────────────────────────
def run_actor(subreddit):
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {APIFY_API_KEY}",
    }
    payload = {
        "startUrls": [{"url": f"https://www.reddit.com/r/{subreddit}/new/"}],
        "maxItems": MAX_POSTS,
        "type": "posts",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"  ✗ Failed to start for r/{subreddit}: {resp.status_code}")
        return None
    return resp.json()["data"]["id"]


def wait_for_run(run_id, timeout=180):
    url = f"{BASE_URL}/actor-runs/{run_id}"
    headers = {"Authorization": f"Bearer {APIFY_API_KEY}"}
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(url, headers=headers, timeout=30)
        status = resp.json()["data"]["status"]
        if status == "SUCCEEDED":
            return True
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            return False
        time.sleep(5)
    return False


def get_results(run_id):
    url = f"{BASE_URL}/actor-runs/{run_id}/dataset/items"
    headers = {"Authorization": f"Bearer {APIFY_API_KEY}"}
    resp = requests.get(url, headers=headers, timeout=30)
    return resp.json() if resp.status_code == 200 else []


def is_fresh(date_str):
    if not date_str:
        return False
    try:
        if isinstance(date_str, (int, float)):
            post_time = datetime.fromtimestamp(date_str, tz=timezone.utc)
        else:
            post_time = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        return post_time >= datetime.now(timezone.utc) - timedelta(hours=HOURS_FRESH)
    except Exception:
        return True


def get_age_label(date_str):
    try:
        if isinstance(date_str, (int, float)):
            post_time = datetime.fromtimestamp(date_str, tz=timezone.utc)
        else:
            post_time = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        hours = int((datetime.now(timezone.utc) - post_time).total_seconds() / 3600)
        return f"{hours}h ago" if hours < 48 else f"{hours//24}d ago"
    except Exception:
        return "Unknown"


def is_bot(author):
    bots = ["automoderator", "bot", "automod"]
    return any(b in str(author).lower() for b in bots)


def scan_subreddit(subreddit, post_type, intent_keywords, seen_urls, existing_links):
    """Scan a subreddit, filter for intent, score with Gemini, return qualified leads."""
    print(f"  📡 r/{subreddit}...")
    run_id = run_actor(subreddit)
    if not run_id or not wait_for_run(run_id):
        print(f"     ✗ Failed")
        return []

    results = get_results(run_id)
    leads = []

    for post in results:
        # Skip bots
        author = post.get("author") or post.get("username") or ""
        if is_bot(author):
            continue

        # Skip stale posts
        date_str = post.get("createdAt") or post.get("created_utc") or ""
        if not is_fresh(date_str):
            continue

        # Skip duplicates
        url = (post.get("url") or post.get("postUrl") or "").strip()
        if not url or url in seen_urls or url in existing_links:
            continue

        # Must be an original post not a comment
        title = (post.get("title") or "").strip()
        if not title:
            continue

        snippet = (post.get("body") or post.get("selftext") or post.get("text") or "").strip()
        combined = (title + " " + snippet).lower()

        # Quick pre-filter — must contain at least one intent keyword
        if not any(kw.lower() in combined for kw in intent_keywords):
            continue

        # Score with Gemini
        score = score_with_gemini(title, snippet, post_type)
        if score < MIN_GEMINI_SCORE:
            print(f"     ⚡ Scored {score} — skipped: {title[:60]}")
            continue

        seen_urls.add(url)
        leads.append({
            "type":      post_type,
            "subreddit": f"r/{subreddit}",
            "author":    f"u/{author}",
            "title":     title,
            "snippet":   snippet[:300],
            "url":       url,
            "age":       get_age_label(date_str),
            "score":     score,
        })
        print(f"     ✅ Score {score}: {title[:60]}")

    print(f"     → {len(leads)} qualified lead(s)")
    time.sleep(2)
    return leads


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = datetime.now(timezone.utc) + ist_offset
    print(f"\n{'═'*60}")
    print(f"  JOVENS REDDIT SCRAPER v3")
    print(f"  {now_ist.strftime('%d %b %Y  %I:%M %p IST')}")
    print(f"  Mode: Subreddit scan only + Gemini scoring")
    print(f"  Min score to qualify: {MIN_GEMINI_SCORE}/100")
    print(f"{'═'*60}\n")

    print("  📊 Connecting to Google Sheet...")
    worksheet      = get_sheet()
    existing_links = get_existing_links(worksheet)
    start_number   = get_next_number(worksheet)
    print(f"  ✓ Connected — {len(existing_links)} existing leads in sheet\n")

    seen_urls  = set()
    all_leads  = []

    # YOGA subreddits
    print(f"  🧘 YOGA SUBREDDITS ({len(YOGA_SUBREDDITS)})")
    print(f"  {'─'*50}")
    for sub in YOGA_SUBREDDITS:
        leads = scan_subreddit(sub, "yoga", YOGA_INTENT_KEYWORDS, seen_urls, existing_links)
        all_leads.extend(leads)

    # PIANO subreddits
    print(f"\n  🎹 PIANO SUBREDDITS ({len(PIANO_SUBREDDITS)})")
    print(f"  {'─'*50}")
    for sub in PIANO_SUBREDDITS:
        leads = scan_subreddit(sub, "piano", PIANO_INTENT_KEYWORDS, seen_urls, existing_links)
        all_leads.extend(leads)

    # Write to sheet
    print(f"\n  📝 Writing {len(all_leads)} qualified lead(s) to sheet...")
    new_count = write_leads(worksheet, all_leads, existing_links, start_number)

    yoga_count  = sum(1 for l in all_leads if l["type"] == "yoga")
    piano_count = sum(1 for l in all_leads if l["type"] == "piano")

    print(f"\n{'═'*60}")
    print(f"  DONE")
    print(f"  🧘 Yoga leads  : {yoga_count}")
    print(f"  🎹 Piano leads : {piano_count}")
    print(f"  📊 New rows    : {new_count}")
    print(f"  Open sheet → filter Status = New → reply to leads")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
