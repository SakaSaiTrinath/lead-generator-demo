# Tests

Covers both tools with three layers:

| File                               | What it covers                                  |
| ---------------------------------- | ----------------------------------------------- |
| `test_scraper_unit.py`             | `scraper/` pure helpers: argparse, URL builder, CSV, `main()` exit codes |
| `test_agent_unit.py`               | `agent/` pure helpers: regex, `normalize_site`, CSV, `main()` |
| `test_scraper_playwright.py`       | Real Chromium driving HTML fixtures that mimic Google Maps DOM |
| `test_agent_playwright.py`         | Real Chromium driving HTML fixtures that mimic a directory / profile |
| `test_end_to_end.py`               | Full pipelines: extract → save_csv → read back; failure resilience |

## Install test-only deps

```bash
pip install pytest playwright rich
python -m playwright install chromium
```

## Run

```bash
python -m pytest
```

The Playwright tests serve `tests/fixtures/*.html` over a tiny
`http.server` spun up per session, so no internet access is required.

If your Chromium lives somewhere unusual, point the suite at it:

```bash
PW_CHROMIUM_PATH=/path/to/chrome python -m pytest
```

Expected output:

```
77 passed in ~4s
```
