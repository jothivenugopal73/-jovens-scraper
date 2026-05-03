import os
import json
import time
import requests
from datetime import datetime
import google.generativeai as genai
from google.oauth2.service_account import Credentials
import gspread

# ── ENV VARS ──────────────────────────────────────────────────────────────────
APIFY_API_KEY         = os.environ["APIFY_API_KEY"]
GEMINI_API_KEY        = os.environ["GEMINI_API_KEY"]
GOOGLE_CREDENTIALS    = os.environ["GOOGLE_CREDENTIALS"]  # JSON string
MEDSOLUTIONS_SHEET_ID = os.environ["MEDSOLUTIONS_SHEET_ID"]

SHEET_TAB = "MedSolutions_Leads"
ACTOR_ID  = "harvestapi/linkedin-profile-search"
APIFY_URL = "https://api.apify.com/v2"

# Search targets — specialty label : LinkedIn search query
SEARCH_TARGETS = {
    "Chiropractic":     "Chiropractor Owner Dallas Texas",
    "Behavioral Health": "Therapist LCSW Psychiatrist Owner Dallas Texas",
    "Primary Care":     "Family Medicine Primary Care Physician Owner Dallas Texas",
}

MAX_PROFILES_PER_QUERY = 25  # Short mode ~$0.10 per search
LOCATION_FILTER        = "Dallas, Texas"

# Column order must match MedSolutions_Leads sheet exactly
COLUMNS = [
    "Date Found",       # A
    "First Name",       # B
    "Last Name",        # C
    "Full Name",        # D
    "Practice Name",    # E
    "Specialty",        # F
    "Title",            # G
    "Location",         # H
    "LinkedIn URL",     # I
    "Company LinkedIn", # J
    "Summary Snippet",  # K
    "Gemini Score",     # L
    "Lead Temperature", # M
    "Gemini Reason",    # N
    "Outreach Status",  # O
    "Outreach Date",    # P
    "Outreach Channel", # Q
    "Contact Email",    # R
    "Notes",            # S
]

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes     = ["https://www.googleapis.com/auth/spreadsheets"]
    creds      = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client     = gspread.authorize(creds)
    return client.open_by_key(MEDSOLUTIONS_SHEET_ID).worksheet(SHEET_TAB)


def get_existing_linkedin_urls(sheet) -> set:
    """
    Pull all LinkedIn URLs already in column I (index 8, 0-based).
    This is how we prevent duplicates across every run.
    """
    all_values = sheet.get_all_values()
    existing   = set()
    for row in all_values[1:]:  # skip header
        if len(row) > 8 and row[8].strip():
            existing.add(row[8].strip().lower())
    print(f"  Sheet already has {len(existing)} existing LinkedIn URLs")
    return existing


def append_rows(sheet, rows: list[list]):
    """Append rows to bottom of sheet — never overwrites."""
    if not rows:
        return
    sheet.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"  Appended {len(rows)} new rows to sheet")


# ── APIFY ─────────────────────────────────────────────────────────────────────
def run_apify_search(query: str) -> list[dict]:
    """Run LinkedIn Profile Search actor and return results list."""
    print(f"\n  Apify searching: '{query}'")

    # Start the run
    resp = requests.post(
        f"{APIFY_URL}/acts/{ACTOR_ID}/runs",
        params={"token": APIFY_API_KEY},
        json={
            "query":           query,
            "mode":            "short",
            "count":           MAX_PROFILES_PER_QUERY,
            "locationsFilter": [LOCATION_FILTER],
        },
        timeout=30,
    )
    resp.raise_for_status()
    run_id = resp.json()["data"]["id"]
    print(f"    Run ID: {run_id} — waiting for completion...")

    # Poll until done
    for attempt in range(40):
        time.sleep(15)
        status_resp = requests.get(
            f"{APIFY_URL}/actor-runs/{run_id}",
            params={"token": APIFY_API_KEY},
            timeout=15,
        )
        status = status_resp.json()["data"]["status"]
        print(f"    [{attempt + 1}] Status: {status}")

        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            print(f"    Run ended with: {status} — skipping this query")
            return []

    # Fetch results
    dataset_id = status_resp.json()["data"]["defaultDatasetId"]
    items_resp = requests.get(
        f"{APIFY_URL}/datasets/{dataset_id}/items",
        params={"token": APIFY_API_KEY, "format": "json"},
        timeout=30,
    )
    items_resp.raise_for_status()
    profiles = items_resp.json()
    print(f"    Returned {len(profiles)} profiles")
    return profiles


# ── GEMINI SCORER ─────────────────────────────────────────────────────────────
def score_with_gemini(first: str, last: str, title: str,
                      company: str, summary: str, specialty: str) -> dict:
    """
    Score lead 0-100 for medical billing outsourcing likelihood.
    Returns dict with score, temperature, reason.
    """
    genai.configure(api_key=GEMINI_API_KEY)
    gemini = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""
You are a medical billing sales analyst for Jovens MedSolutions,
a DFW-based billing company targeting small independent practices with 1-3 doctors.

Score this LinkedIn profile 0-100 for likelihood they need to outsource medical billing.

PROFILE:
Name: {first} {last}
Title: {title}
Practice: {company}
Specialty: {specialty}
Bio: {summary[:800]}

SCORING RULES:
95-100: New practice opening soon AND will need billing from day one
85-94:  Solo owner explicitly handles billing themselves OR mentions insurance/denial pain
70-84:  Solo owner/founder of small practice, complex insurance mix (PIP, Medicare, EAP)
50-69:  Owner/partner at small practice, no clear billing signal
30-49:  Employee at a clinic, not the decision maker
0-29:   Works at hospital, health system, large group, or chain — wrong target

Respond ONLY in this exact JSON format, no extra text:
{{
  "score": <integer 0-100>,
  "temperature": "<Hot|Warm|Cold>",
  "reason": "<one sentence explaining the score>"
}}

Temperature guide: Hot = score 80+, Warm = score 60-79, Cold = score below 60
"""

    try:
        response = gemini.generate_content(prompt)
        text     = response.text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        return {
            "score":       int(result.get("score", 0)),
            "temperature": result.get("temperature", "Cold"),
            "reason":      result.get("reason", ""),
        }
    except Exception as e:
        print(f"    Gemini error: {e}")
        return {"score": 0, "temperature": "Cold", "reason": "Gemini scoring failed"}


# ── PROFILE PARSER ────────────────────────────────────────────────────────────
def parse_profile(profile: dict, specialty: str) -> dict:
    """Extract clean fields from raw Apify profile dict."""
    positions   = profile.get("currentPositions") or []
    first_pos   = positions[0] if positions else {}

    first       = (profile.get("firstName") or "").strip()
    last        = (profile.get("lastName") or "").strip()
    title       = (first_pos.get("title") or "").strip()
    company     = (first_pos.get("companyName") or "").strip()
    company_url = (first_pos.get("companyLinkedinUrl") or "").strip()
    linkedin    = (profile.get("linkedinUrl") or "").strip()
    location    = (profile.get("location", {}) or {})
    loc_text    = location.get("linkedinText", "") if isinstance(location, dict) else str(location)
    summary     = (profile.get("summary") or "").strip()

    return {
        "first":       first,
        "last":        last,
        "full_name":   f"{first} {last}".strip(),
        "title":       title,
        "company":     company,
        "company_url": company_url,
        "linkedin":    linkedin,
        "location":    loc_text,
        "summary":     summary,
        "specialty":   specialty,
    }


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("Jovens MedSolutions — LinkedIn Lead Scraper")
    print(f"Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Connect to sheet and load existing URLs for dedup
    print("\nConnecting to Google Sheet...")
    sheet        = get_sheet()
    existing_urls = get_existing_linkedin_urls(sheet)

    total_new    = 0
    total_skipped = 0

    for specialty, query in SEARCH_TARGETS.items():
        print(f"\n{'─' * 50}")
        print(f"Specialty: {specialty}")
        print(f"{'─' * 50}")

        profiles = run_apify_search(query)
        if not profiles:
            print("  No profiles returned — skipping")
            continue

        new_rows = []

        for profile in profiles:
            parsed  = parse_profile(profile, specialty)
            linkedin = parsed["linkedin"].lower().strip()

            # ── DEDUP CHECK ──────────────────────────────────────────────────
            if not linkedin:
                print(f"  SKIP — no LinkedIn URL: {parsed['full_name']}")
                total_skipped += 1
                continue

            if linkedin in existing_urls:
                print(f"  SKIP — already in sheet: {parsed['full_name']}")
                total_skipped += 1
                continue

            # ── GEMINI SCORE ─────────────────────────────────────────────────
            print(f"  Scoring: {parsed['full_name']} — {parsed['company']}")
            scored = score_with_gemini(
                parsed["first"], parsed["last"],
                parsed["title"], parsed["company"],
                parsed["summary"], specialty,
            )
            print(f"    Score: {scored['score']} | {scored['temperature']} | {scored['reason'][:60]}...")

            # ── BUILD ROW (must match COLUMNS order exactly) ─────────────────
            row = [
                datetime.now().strftime("%Y-%m-%d"),  # Date Found
                parsed["first"],                       # First Name
                parsed["last"],                        # Last Name
                parsed["full_name"],                   # Full Name
                parsed["company"],                     # Practice Name
                specialty,                             # Specialty
                parsed["title"],                       # Title
                parsed["location"],                    # Location
                parsed["linkedin"],                    # LinkedIn URL
                parsed["company_url"],                 # Company LinkedIn
                parsed["summary"][:300],               # Summary Snippet
                scored["score"],                       # Gemini Score
                scored["temperature"],                 # Lead Temperature
                scored["reason"],                      # Gemini Reason
                "New",                                 # Outreach Status
                "",                                    # Outreach Date
                "",                                    # Outreach Channel
                "",                                    # Contact Email
                "",                                    # Notes
            ]

            new_rows.append(row)
            # Add to local set so within-run duplicates are also caught
            existing_urls.add(linkedin)

            # Small delay to be polite to Gemini API
            time.sleep(1)

        # Append this specialty's new rows to sheet
        if new_rows:
            append_rows(sheet, new_rows)
            total_new += len(new_rows)
        else:
            print("  No new leads for this specialty")

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"Run complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"New leads added : {total_new}")
    print(f"Duplicates skipped: {total_skipped}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
