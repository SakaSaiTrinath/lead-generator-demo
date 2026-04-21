# Agentic Lead Finder

A Playwright-powered crawler that acts agentically: it navigates a target
site (directory, marketplace, portfolio platform), finds a search bar or
falls back to a Google `site:` query, then follows company-profile links
up to N levels deep to extract contact information.

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python lead_agent.py \
    --keyword "animation studio" \
    --site "clutch.co" \
    --max-results 20
```

### Flags

| Flag            | Default | Description                                           |
| --------------- | ------- | ----------------------------------------------------- |
| `--keyword`     | —       | What to search the site for                           |
| `--site`        | —       | Domain to crawl (e.g. `clutch.co`, `yellowpages.ca`)  |
| `--max-results` | `20`    | Hard cap on collected leads                           |
| `--depth`       | `2`     | Crawl depth (1–3). 3 = follow profile sub-pages too   |
| `--headless`    | `True`  | Set to `False` to watch the browser work              |

## Output

CSV written to `../output/agent_leads_<site>_<keyword>_<timestamp>.csv`
with columns:

- `company_name`
- `website`
- `email`
- `phone`
- `location`
- `description`
- `source_url`

A live `rich` progress bar reports crawl progress.
