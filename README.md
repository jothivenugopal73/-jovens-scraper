# Jovens MedSolutions — LinkedIn Lead Scraper

Scrapes LinkedIn for small DFW medical practices likely to need 
outsourced medical billing services.

## Target Specialties
- Chiropractic (Owner/Founder practices)
- Behavioral Health (Therapist, LCSW, Psychiatrist)
- Primary Care (Family Medicine, Internal Medicine)

## How It Works
1. Apify `harvestapi/linkedin-profile-search` finds practice owners in Dallas TX
2. Gemini 2.0 Flash scores each lead 0-100 for billing outsourcing likelihood
3. New leads (deduped by LinkedIn URL) appended to Google Sheet

## How To Run
Go to Railway → service → Deploy manually
No cron — runs only when triggered manually

## Google Sheet
MedSolutions_Leads tab — never overwrites, always appends

## Environment Variables
- APIFY_API_KEY
- GEMINI_API_KEY
- GOOGLE_CREDENTIALS
- MEDSOLUTIONS_SHEET_ID

## Target Sheet
1iXScsV25JewA9Ngf_t-nQP2aalrIsbcZnpX3hEqlofM
