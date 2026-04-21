# Google Maps Scraper

A Playwright-powered scraper that queries Google Maps for a business keyword
inside a target location and exports the listings as CSV.

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python google_maps_scraper.py \
    --keyword "VFX studio" \
    --location "Toronto" \
    --max-results 20
```

### Flags

| Flag            | Default | Description                                    |
| --------------- | ------- | ---------------------------------------------- |
| `--keyword`     | —       | Business keyword (e.g. `"VFX studio"`)         |
| `--location`    | —       | City or region (e.g. `"Toronto"`)              |
| `--max-results` | `20`    | How many listings to collect                   |
| `--headless`    | `True`  | Set to `False` to watch the browser work       |

Run visibly:

```bash
python google_maps_scraper.py --keyword "film production" --location "Berlin" --headless False
```

## Output

A CSV is written to `../output/leads_<keyword>_<location>_<timestamp>.csv`
with columns:

- `company_name`
- `website`
- `phone`
- `address`
- `google_maps_url`
- `category`

A formatted table is also printed to stdout with `rich`.
