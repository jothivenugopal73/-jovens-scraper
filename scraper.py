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
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
SHEET_TAB_NAME     = "FB_Yoga_Leads"

HOURS_FRESH        = 48
MAX_RESULTS        = 50
MIN_SCORE          = 65

BASE_URL  = "https://api.apify.com/v2"
ACTOR_ID  = "powerai~facebook-post-search-scraper"

# ─────────────────────────────────────────────
# HIGH INTENT SEARCH KEYWORDS
# ─────────────────────────────────────────────
YOGA_SEARCHES = [
    "looking for yoga classes online USA",
    "need yoga instructor online United States",
    "beginner yoga help online US",
]
MAX_RESULTS = 20

# ─────────────────────────────────────────────
# IGNORE FILTERS — skip these types of posts
# ─────────────────────────────────────────────
IGNORE_PHRASES = [
    "motivational", "inspiration", "quote", "blessed",
    "good morning", "good evening", "good night",
    "follow me", "check out my", "shop now", "buy now",
    "blooming", "nature", "mountain", "travel",
    "relationship", "god", "prayer", "faith",
    "meme", "funny", "lol", "haha",
    "dump", "memories", "throwback",
]

# ─────────────────────────────────────────────
# GOOGLE SHEET
# ─────────────────────────────────────────────
SHEET_HEADERS = [
    "#", "Date Found", "Time (IST)", "Post Age",
    "Author Name", "Group / Page",
    "Post Content", "Post Link",
    "Reactions", "Comments",
    "Gemini Score", "Intent Label",
    "Status", "Outreach Date", "Response", "Notes",
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
        worksheet.format(f"A1:P1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.12, "green": 0.53, "blue": 0.27},
            "horizontalAlignment": "CENTER",
        })
        print(f"  ✓ Created new tab: {SHEET_TAB_NAME}")
    return worksheet


def get_existing_links(worksheet):
    try:
        col_index = SHEET_HEADERS.index("Post Link") + 1
        links = worksheet.col_values(col_index)
        return set(v.strip() for v in links[1:] if v.strip())
    except Exception:
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

        # intent label
        score = lead["score"]
        if score >= 80:
            label = "🔥 Hot"
        elif score >= 65:
            label = "🟡 Warm"
        else:
            label = "❄️ Cold"

        row = [
            counter,
            now_ist.strftime("%d-%b-%Y"),
            now_ist.strftime("%I:%M %p"),
            lead["age"],
            lead["author"],
            lead["group"],
            lead["message"][:500],
            url,
            lead["reactions"],
            lead["comments"],
            score,
            label,
            "New",
            "", "", "",
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
def score_with_gemini(message):
    """Score a Facebook post 0-100 for yoga class buying intent."""
    if not GEMINI_API_KEY:
        return keyword_score(message)

    prompt = f"""You are a lead qualification expert for Jovens Yoga — a live online group yoga school for US students.

Score this Facebook post from 0 to 100 based on how likely the person is genuinely looking for an online yoga class.

Scoring guide:
90-100 = Explicitly asking for online yoga class, teacher, or recommendation. High urgency.
75-89  = Clearly interested in yoga online, asking for help or options.
65-74  = Mentions wanting yoga online but vague or exploratory.
40-64  = Yoga mentioned but not clearly looking for a class.
Below 40 = Not relevant — motivational content, business promotion, unrelated topic.

Post: {message[:600]}

Reply with ONLY a number 0-100. Nothing else."""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            digits = ''.join(filter(str.isdigit, text))[:3]
            if digits:
                return min(100, max(0, int(digits)))
    except Exception:
        pass
    return keyword_score(message)


def keyword_score(message):
    """Fallback keyword scoring."""
    msg = message.lower()
    high_intent = [
        "looking for yoga", "need yoga", "yoga class", "yoga teacher",
        "yoga instructor", "yoga online", "online yoga", "yoga recommendation",
        "yoga classes", "beginner yoga", "yoga zoom", "yoga for",
        "learn yoga", "yoga help", "yoga program",
    ]
    low_intent = [
        "selling", "offering", "check out", "follow", "shop",
        "buy", "promo", "discount", "deal",
    ]
    matched_high = sum(1 for kw in high_intent if kw in msg)
    matched_low  = sum(1 for kw in low_intent if kw in msg)

    if matched_low > 0:
        return 20
    if matched_high >= 3:
        return 85
    if matched_high == 2:
        return 72
    if matched_high == 1:
        return 58
    return 25


def is_noise(message):
    """Return True if this post is clearly irrelevant noise."""
    msg = message.lower()
    noise_count = sum(1 for phrase in IGNORE_PHRASES if phrase in msg)
    return noise_count >= 2


# ─────────────────────────────────────────────
# APIFY
# ─────────────────────────────────────────────
def run_actor(search_query):
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {APIFY_API_KEY}",
    }
    payload = {
        "search_query": search_query,
        "recent_posts": True,
        "max_results": MAX_RESULTS,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"  ✗ Failed for '{search_query}': {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json()["data"]["id"]


def wait_for_run(run_id, timeout=300):
    url = f"{BASE_URL}/actor-runs/{run_id}"
    headers = {"Authorization": f"Bearer {APIFY_API_KEY}"}
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(url, headers=headers, timeout=30)
        status = resp.json()["data"]["status"]
        if status == "SUCCEEDED":
            return True
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            print(f"  ✗ Run ended: {status}")
            return False
        time.sleep(5)
    print(f"  ✗ Timed out")
    return False


def get_results(run_id):
    url = f"{BASE_URL}/actor-runs/{run_id}/dataset/items"
    headers = {"Authorization": f"Bearer {APIFY_API_KEY}"}
    resp = requests.get(url, headers=headers, timeout=30)
    return resp.json() if resp.status_code == 200 else []


def get_age_label(timestamp):
    try:
        if isinstance(timestamp, (int, float)):
            post_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            post_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        hours = int((datetime.now(timezone.utc) - post_time).total_seconds() / 3600)
        return f"{hours}h ago" if hours < 48 else f"{hours//24}d ago"
    except Exception:
        return "Unknown"


def is_fresh(timestamp):
    try:
        if isinstance(timestamp, (int, float)):
            post_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            post_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return post_time >= datetime.now(timezone.utc) - timedelta(hours=HOURS_FRESH)
    except Exception:
        return True


def format_post(post, keyword):
    message   = (post.get("message") or post.get("message_rich") or "").strip()
    url       = (post.get("url") or "").strip()
    author    = post.get("author", {}).get("name") or "Unknown"
    group_id  = post.get("associated_group_id") or ""
    group     = f"Group {group_id}" if group_id else "Facebook"
    timestamp = post.get("timestamp") or ""
    reactions = post.get("reactions_count") or 0
    comments  = post.get("comments_count") or 0

    return {
        "keyword":   keyword,
        "message":   message,
        "url":       url,
        "author":    author,
        "group":     group,
        "age":       get_age_label(timestamp),
        "timestamp": timestamp,
        "reactions": reactions,
        "comments":  comments,
        "score":     0,
    }


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = datetime.now(timezone.utc) + ist_offset
    print(f"\n{'═'*60}")
    print(f"  JOVENS FACEBOOK YOGA LEAD SCRAPER")
    print(f"  {now_ist.strftime('%d %b %Y  %I:%M %p IST')}")
    print(f"  Searching {len(YOGA_SEARCHES)} keywords on Facebook")
    print(f"  Min Gemini score: {MIN_SCORE}/100")
    print(f"{'═'*60}\n")

    print("  📊 Connecting to Google Sheet...")
    worksheet      = get_sheet()
    existing_links = get_existing_links(worksheet)
    start_number   = get_next_number(worksheet)
    print(f"  ✓ Connected — {len(existing_links)} existing leads in sheet\n")

    seen_urls  = set()
    all_leads  = []

    for keyword in YOGA_SEARCHES:
        print(f"  🔍 \"{keyword}\"...")
        run_id = run_actor(keyword)
        if not run_id:
            continue
        if not wait_for_run(run_id):
            continue

        results  = get_results(run_id)
        count    = 0
        skipped  = 0

        for post in results:
            message = (post.get("message") or post.get("message_rich") or "").strip()
            url     = (post.get("url") or "").strip()

            # Skip empty
            if not message or not url:
                continue

            # Skip duplicates
            if url in seen_urls or url in existing_links:
                continue

            # Skip stale
            if not is_fresh(post.get("timestamp") or ""):
                skipped += 1
                continue

            # Skip obvious noise
            if is_noise(message):
                skipped += 1
                continue

            # Score with Gemini
            score = score_with_gemini(message)
            if score < MIN_SCORE:
                print(f"     ⚡ Score {score} — skipped: {message[:60]}")
                skipped += 1
                continue

            lead = format_post(post, keyword)
            lead["score"] = score
            seen_urls.add(url)
            all_leads.append(lead)
            count += 1
            print(f"     ✅ Score {score}: {message[:70]}")

        print(f"     → {count} qualified, {skipped} skipped")
        time.sleep(3)

    # Write all to sheet
    print(f"\n  📝 Writing {len(all_leads)} qualified lead(s) to sheet...")
    new_count = write_leads(worksheet, all_leads, existing_links, start_number)

    hot   = sum(1 for l in all_leads if l["score"] >= 80)
    warm  = sum(1 for l in all_leads if 65 <= l["score"] < 80)

    print(f"\n{'═'*60}")
    print(f"  DONE")
    print(f"  🔥 Hot leads  : {hot}")
    print(f"  🟡 Warm leads : {warm}")
    print(f"  📊 New rows   : {new_count}")
    print(f"  Open FB_Yoga_Leads tab → reply to Hot leads within 2 hrs")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
