# Lead Generation Demo — VFX Studio Cold Email

Two complementary lead-gen tools built for a VFX studio's outbound pipeline.
Both are pure Python, use Playwright's headless Chromium, and require no
paid APIs or API keys.

```
lead-generator-demo/
├── scraper/          # Google Maps scraper
├── agent/            # Agentic crawler (search + depth)
├── output/           # CSVs land here
├── SUMMARY.md        # this file
└── .env.example
```

---

## 1. What each tool does

### `scraper/google_maps_scraper.py`
Searches Google Maps for `"<keyword> in <location>"`, scrolls the results
feed until it has enough listings, then opens each card and extracts the
business name, website, phone, address, Google Maps URL, and category.
Best for geographic prospecting of businesses that have claimed a Google
Business profile (studios, agencies, production houses in a city).

### `agent/lead_agent.py`
An agentic crawler: it loads a directory or marketplace site (e.g.
`clutch.co`, `yellowpages.ca`), tries to find a visible search input and
submit the keyword, and falls back to a Google `site:<domain> <keyword>`
query if no search box is detectable. It then BFS-crawls links that look
like company profiles up to 3 levels deep, extracting name, website,
email (via regex), phone (via regex), location, and a 200-char
description. Best for directory-style sources that surface contact info
Google Maps doesn't.

---

## 2. Install

Both folders have their own `requirements.txt`. Install each in its own
venv or share one — the dependency list is identical.

```bash
# from the repo root
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Scraper deps
pip install -r scraper/requirements.txt

# Agent deps (same set; safe to install either way)
pip install -r agent/requirements.txt

# One-time Playwright browser install
python -m playwright install chromium
```

Python 3.10+ is required.

Copy `.env.example` to `.env` if you want to record defaults — the scripts
themselves take config via CLI flags.

### Live smoke test

Once installed, run both tools end-to-end against real targets:

```bash
python scripts/live_smoke.py                 # small run (3 leads each)
python scripts/live_smoke.py --headful       # watch the browser
python scripts/live_smoke.py --scraper-only  # only the Google Maps tool
python scripts/live_smoke.py --agent-only --site yellowpages.ca
```

The script does a pre-flight (Python, deps, Chromium), runs each tool
with a small `--max-results`, and validates that a timestamped CSV with
the expected columns and at least one row landed in `output/`. Prints a
human verification checklist at the end.

---

## 3. Example CLI commands

### Google Maps scraper

```bash
# VFX studios in Toronto
python scraper/google_maps_scraper.py \
    --keyword "VFX studio" \
    --location "Toronto" \
    --max-results 20

# Run visibly (handy for debugging)
python scraper/google_maps_scraper.py \
    --keyword "film production" \
    --location "Berlin" \
    --max-results 30 \
    --headless False
```

### Agentic lead finder

```bash
# Animation studios on Clutch
python agent/lead_agent.py \
    --keyword "animation studio" \
    --site "clutch.co" \
    --max-results 20 \
    --depth 2

# Post-production houses on Yellow Pages, deeper crawl
python agent/lead_agent.py \
    --keyword "post production" \
    --site "yellowpages.ca" \
    --max-results 25 \
    --depth 3
```

---

## 4. Sample output format

Both tools write CSVs under `output/` with a timestamped filename.

### `output/leads_<keyword>_<location>_<timestamp>.csv` (scraper)

| Column            | Example                              |
| ----------------- | ------------------------------------ |
| `company_name`    | `Pixel Grove VFX`                    |
| `website`         | `https://pixelgrovefx.com`           |
| `phone`           | `+1 416-555-0134`                    |
| `address`         | `123 King St W, Toronto, ON M5V 1J2` |
| `google_maps_url` | `https://www.google.com/maps/place/…`|
| `category`        | `Visual effects studio`              |

### `output/agent_leads_<site>_<keyword>_<timestamp>.csv` (agent)

| Column         | Example                                                |
| -------------- | ------------------------------------------------------ |
| `company_name` | `Northern Lights Animation`                            |
| `website`      | `https://northernlightsanim.com`                       |
| `email`        | `hello@northernlightsanim.com`                         |
| `phone`        | `+1 604-555-0198`                                      |
| `location`     | `Vancouver, BC`                                        |
| `description`  | `Boutique 2D/3D animation studio specialising in…`     |
| `source_url`   | `https://clutch.co/profile/northern-lights-animation`  |

Any field that can't be located is saved as an empty string — no row is
dropped for missing data except when the company name itself can't be
extracted.

---

## 5. Known limitations & edge cases

- **Google Maps DOM churn.** Google rewrites its Maps markup a few times
  a year. The scraper sticks to stable `data-item-id` / `role="feed"`
  hooks, but if it returns zero results, run it with `--headless False`
  and inspect the panel.
- **Consent walls.** Google shows different cookie consent UIs per
  region. The code clicks a handful of common variants, but
  EU visitors on fresh profiles may occasionally hit a variant that
  blocks scraping — rerunning once usually gets past it.
- **Search-bar fingerprinting.** The agent tries a short list of
  generic selectors to find a site's search input. Sites that render
  their search as a custom element may silently fall through to the
  Google fallback — that's by design.
- **Google rate-limiting the fallback.** Repeated `site:` queries from
  the same IP will eventually trigger a CAPTCHA. The 1.5–3.5 s polite
  sleep helps but doesn't prevent this at scale — for heavy use,
  rotate IPs or swap the fallback for a dedicated SERP provider.
- **Regex-based email / phone extraction.** Simple, fast, and
  surprisingly effective, but will miss obfuscated formats
  (`hello [at] domain [dot] com`) and can occasionally false-positive
  on tracking IDs. A blocklist filters the obvious junk.
- **JS-only contact pages.** Some profiles reveal emails/phones only
  after clicking a "show contact" button. These tools do not attempt
  those interactions — a future enhancement.
- **Legal / ToS.** Both Google Maps and many directories prohibit
  scraping in their terms of service. Use this demo for
  internal research and cold-outreach prep on targets you have
  legitimate reason to contact.

---

## 6. When to use which

| You want…                                                              | Use        |
| ---------------------------------------------------------------------- | ---------- |
| A list of VFX studios / animation houses **in a specific city**        | scraper    |
| Phones and addresses for a **walk-in / local** campaign                | scraper    |
| A fast, shallow pass over an entire region                             | scraper    |
| **Emails** for cold-email outreach                                     | agent      |
| Coverage of **niche directories** (Clutch, The Mill list, YP, etc.)    | agent      |
| Richer **descriptions / positioning** for personalized emails          | agent      |
| Targets that don't have Google Business profiles                       | agent      |

In practice, run both: use the scraper to seed the geographic list, then
point the agent at an industry directory to top up emails and
descriptions for the names the scraper returned.
