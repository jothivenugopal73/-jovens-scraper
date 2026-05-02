import os
import json
import time
import requests
import gspread
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials


# ─────────────────────────────────────────────
# CONFIG — all values come from Railway env vars
# ─────────────────────────────────────────────
APIFY_API_KEY      = os.environ["APIFY_API_KEY"]
GOOGLE_SHEET_ID    = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDENTIALS = os.environ["GOOGLE_CREDENTIALS"]
SHEET_TAB_NAME     = "Social_Leads"

HOURS_FRESH        = 48
MAX_POSTS          = 25

# ─────────────────────────────────────────────
# WHAT WE SEARCH
# ─────────────────────────────────────────────
YOGA_SEARCHES = [
    "online yoga classes",
    "yoga teacher online",
    "yoga lessons online",
    "beginner yoga online",
    "live yoga class online",
]

PIANO_SEARCHES = [
    "learn piano online",
    "piano lessons online",
    "online piano teacher",
    "piano classes for kids online",
    "online music lessons kids",
]

YOGA_SUBREDDITS = [
    "yoga", "fitness", "xxfitness", "onlinefitness", "mommit", "beyondthebump",
]

PIANO_SUBREDDITS = [
    "pianolessons", "piano", "learnpiano", "Parenting", "homeschool", "kidsactivities",
]

BASE_URL = "https://api.apify.com/v2"
ACTOR_ID = "trudax~reddit-scraper-lite"

# ─────────────────────────────────────────────
# GOOGLE SHEET
# ─────────────────────────────────────────────
SHEET_HEADERS = [
    "#", "Date Found", "Time (IST)", "Post Age", "Platform",
    "Subreddit / Source", "Author / Handle", "Post Title",
    "Post Content Snippet", "Post Link", "Business", "Keyword Matched",
    "Status", "Outreach Method", "Outreach Date", "Response", "Outcome / Notes",
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
            title=SHEET_TAB_NAME, rows=2000, cols=len(SHEET_HEADERS)
        )
        worksheet.append_row(SHEET_HEADERS)
        worksheet.format(f"A1:Q1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.17, "green": 0.24, "blue": 0.48},
            "horizontalAlignment": "CENTER",
        })
        print(f"  ✓ Created new tab: {SHEET_TAB_NAME}")
    return worksheet


def get_existing_links(worksheet):
    try:
        col_index = SHEET_HEADERS.index("Post Link") + 1
        links = worksheet.col_values(col_index)
        return set(links[1:])
    except Exception:
        return set()


def get_next_number(worksheet):
    try:
        col_values = worksheet.col_values(1)
        numbers = [int(v) for v in col_values[1:] if str(v).isdigit()]
        return max(numbers) + 1 if numbers else 1
    except Exception:
        return 1


def write_to_sheet(worksheet, posts, existing_links, start_number):
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = datetime.now(timezone.utc) + ist_offset
    rows_to_add = []
    counter = start_number
    for post in posts:
        if post["url"] in existing_links:
            continue
        business = "🧘 Yoga" if post["type"] == "yoga" else "🎹 Piano"
        row = [
            counter,
            now_ist.strftime("%d-%b-%Y"),
            now_ist.strftime("%I:%M %p"),
            post["age"],
            "Reddit",
            post["subreddit"],
            post["author"],
            post["title"][:150],
            post["text"][:300],
            post["url"],
            business,
            post["keyword"],
            "New",
            "", "", "", "",
        ]
        rows_to_add.append(row)
        existing_links.add(post["url"])
        counter += 1
    if rows_to_add:
        worksheet.append_rows(rows_to_add, value_input_option="USER_ENTERED")
        print(f"  ✓ {len(rows_to_add)} new lead(s) written to sheet")
    else:
        print("  ℹ No new leads to write")
    return len(rows_to_add)


# ─────────────────────────────────────────────
# APIFY
# ─────────────────────────────────────────────
def run_reddit_actor(search_term=None, subreddit=None):
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {APIFY_API_KEY}",
    }
    if search_term:
        payload = {
            "searches": [search_term],
            "maxItems": MAX_POSTS,
            "sort": "new",
            "time": "day",
            "type": "posts",
        }
    else:
        payload = {
            "startUrls": [{"url": f"https://www.reddit.com/r/{subreddit}/new/"}],
            "maxItems": MAX_POSTS,
            "type": "posts",
        }
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code not in (200, 201):
        label = search_term or subreddit
        print(f"  ✗ Failed for '{label}': {resp.status_code}")
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
        age_hours = int((datetime.now(timezone.utc) - post_time).total_seconds() / 3600)
        return f"{age_hours}h ago" if age_hours < 48 else f"{age_hours//24}d ago"
    except Exception:
        return "Unknown"


def format_post(post, keyword, post_type):
    title  = (post.get("title") or "").strip()
    text   = (post.get("body") or post.get("selftext") or post.get("text") or "").strip()[:300]
    url    = post.get("url") or post.get("postUrl") or ""
    author = post.get("author") or post.get("username") or "unknown"
    sub    = post.get("subreddit") or post.get("communityName") or "unknown"
    date_str = post.get("createdAt") or post.get("created_utc") or ""
    if url and not url.startswith("http"):
        url = f"https://reddit.com{url}"
    return {
        "type":      post_type,
        "keyword":   keyword,
        "title":     title,
        "text":      text,
        "url":       url,
        "author":    f"u/{author}",
        "subreddit": f"r/{sub}",
        "age":       get_age_label(date_str),
        "date_str":  date_str,
    }


def is_relevant(post, keywords):
    combined = (
        (post.get("title") or "") + " " +
        (post.get("body") or post.get("selftext") or post.get("text") or "")
    ).lower()
    return any(kw.lower() in combined for kw in keywords)


def scrape_and_collect(label, searches, subreddits, post_type,
                       relevance_keywords, seen_urls, existing_links):
    posts = []

    print(f"\n  {label} — Keyword searches ({len(searches)})")
    print(f"  {'─'*50}")
    for keyword in searches:
        print(f"  🔍 \"{keyword}\"...")
        run_id = run_reddit_actor(search_term=keyword)
        if not run_id or not wait_for_run(run_id):
            continue
        count = 0
        for post in get_results(run_id):
            date_str = post.get("createdAt") or post.get("created_utc") or ""
            if not is_fresh(date_str):
                continue
            url = post.get("url") or ""
            if url in seen_urls or url in existing_links:
                continue
            seen_urls.add(url)
            posts.append(format_post(post, keyword, post_type))
            count += 1
        print(f"     ✓ {count} fresh post(s)")
        time.sleep(2)

    print(f"\n  {label} — Subreddit scans ({len(subreddits)})")
    print(f"  {'─'*50}")
    for sub in subreddits:
        print(f"  📡 r/{sub}...")
        run_id = run_reddit_actor(subreddit=sub)
        if not run_id or not wait_for_run(run_id):
            continue
        count = 0
        for post in get_results(run_id):
            if not is_relevant(post, relevance_keywords):
                continue
            date_str = post.get("createdAt") or post.get("created_utc") or ""
            if not is_fresh(date_str):
                continue
            url = post.get("url") or ""
            if url in seen_urls or url in existing_links:
                continue
            seen_urls.add(url)
            posts.append(format_post(post, f"r/{sub} scan", post_type))
            count += 1
        print(f"     ✓ {count} relevant fresh post(s)")
        time.sleep(2)

    return posts


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = datetime.now(timezone.utc) + ist_offset
    print(f"\n{'═'*60}")
    print(f"  JOVENS REDDIT SCRAPER")
    print(f"  {now_ist.strftime('%d %b %Y  %I:%M %p IST')}")
    print(f"  Fresh window: last {HOURS_FRESH} hours")
    print(f"{'═'*60}")

    print("\n  📊 Connecting to Google Sheet...")
    worksheet      = get_sheet()
    existing_links = get_existing_links(worksheet)
    start_number   = get_next_number(worksheet)
    print(f"  ✓ Connected — {len(existing_links)} existing leads in sheet")

    seen_urls = set()

    yoga_posts = scrape_and_collect(
        label="🧘 YOGA",
        searches=YOGA_SEARCHES,
        subreddits=YOGA_SUBREDDITS,
        post_type="yoga",
        relevance_keywords=["yoga", "online class", "teacher", "instructor", "lessons", "live class"],
        seen_urls=seen_urls,
        existing_links=existing_links,
    )

    piano_posts = scrape_and_collect(
        label="🎹 PIANO",
        searches=PIANO_SEARCHES,
        subreddits=PIANO_SUBREDDITS,
        post_type="piano",
        relevance_keywords=["piano", "music", "online", "teacher", "lessons", "kids", "learn"],
        seen_urls=seen_urls,
        existing_links=existing_links,
    )

    all_posts = yoga_posts + piano_posts

    print(f"\n  📝 Writing {len(all_posts)} lead(s) to Google Sheet...")
    new_count = write_to_sheet(worksheet, all_posts, existing_links, start_number)

    print(f"\n{'═'*60}")
    print(f"  DONE")
    print(f"  🧘 Yoga leads  : {len(yoga_posts)}")
    print(f"  🎹 Piano leads : {len(piano_posts)}")
    print(f"  📊 New rows added : {new_count}")
    print(f"  Open sheet → filter Status = New → go reply")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
