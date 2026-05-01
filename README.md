# Jovens Social Listening Scraper

Scrapes Facebook for fresh yoga and piano leads daily.
Writes results to Google Sheet automatically every morning.

---

## What it does

- Runs every morning 6:00 AM IST (Monday–Saturday)
- Searches 10 keywords across Facebook
- Filters posts from last 48 hours only
- Deduplicates — never writes the same post twice
- Writes all new leads to the Social_Leads tab in your Google Sheet
- Each lead has Status = "New" so you can filter and action them

---

## Files

| File | Purpose |
|---|---|
| scraper.py | Main script |
| requirements.txt | Python dependencies |
| railway.toml | Railway cron schedule config |

---

## Railway Deployment — Step by Step

### Step 1 — Push to GitHub
Create a new GitHub repo called `jovens-scraper`.
Push these 3 files to it.

### Step 2 — Create Railway project
1. Go to railway.app
2. New Project → Deploy from GitHub repo
3. Select your `jovens-scraper` repo
4. Railway auto-detects Python and installs requirements

### Step 3 — Add Environment Variables
In Railway → your service → Variables tab, add these:

| Variable | Value |
|---|---|
| APIFY_API_KEY | apify_api_jBFzQC1gZWuZxCzWXQYE46WDkrps4M2KruQx |
| GOOGLE_SHEET_ID | 1UwSABfcarmNqK24NQRKpJ3TM3HiQcE4d0uqVyAE3c2w |
| GOOGLE_CREDENTIALS | (paste entire contents of your credentials.json here) |

### Step 4 — Test it manually
In Railway → your service → hit "Deploy" manually once.
Watch the logs. You should see leads being found and written to sheet.

### Step 5 — Verify Google Sheet
Open your Google Sheet.
You should see a new tab called "Social_Leads" with leads populated.

### Step 6 — Cron is live
railway.toml sets it to run automatically every morning 6 AM IST.
No further action needed. Check your sheet each morning.

---

## Google Sheet — Social_Leads tab columns

| Column | What it is |
|---|---|
| # | Lead number |
| Date Found | Date scraped |
| Time (IST) | Time in IST |
| Post Age | How old the post was when found |
| Platform | Facebook |
| Group Name | Which group the post came from |
| Author / Handle | Who posted |
| Post Content Snippet | First 300 chars of their post |
| Post Link | Click this to go directly to the post and reply |
| Business | Yoga or Piano |
| Keyword Matched | Which keyword triggered the capture |
| Status | You update this: New → Replied → Interested → Trial Booked → Paid → Lost |
| Outreach Method | FB Reply / FB DM — you fill this in |
| Outreach Date | When you replied |
| Response | Yes / No / Awaiting |
| Outcome / Notes | Free text — what happened |

---

## Daily routine once live

1. Open Google Sheet — Social_Leads tab
2. Filter Status = "New"
3. Click Post Link for each lead
4. Reply helpfully — no Jovens mention yet
5. Update Status to "Replied"
6. When they respond with interest → introduce Jovens → update Status

---

## Troubleshooting

**No leads showing** — Check Apify account, make sure facebook-posts-scraper actor is saved
**Sheet not updating** — Check GOOGLE_CREDENTIALS env var, make sure service account has edit access to sheet
**Railway not running** — Check railway.toml cron syntax, verify deployment succeeded
