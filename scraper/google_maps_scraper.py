"""Google Maps scraper for business lead generation.

Searches Google Maps for a keyword in a specified location using a headless
Chromium browser, scrolls the results panel to load up to ``--max-results``
listings, and exports the extracted fields (company name, website, phone,
address, Google Maps URL, category) to a timestamped CSV under ``output/``.

Run:
    python google_maps_scraper.py --keyword "VFX studio" --location "Toronto"
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import (
    Browser,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from rich.console import Console
from rich.table import Table


# Project root (parent of the scraper/ folder) — CSVs land in <root>/output/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"

# Column order shared between CSV and stdout table.
FIELDS = [
    "company_name",
    "website",
    "phone",
    "address",
    "google_maps_url",
    "category",
]

console = Console()


@dataclass
class Lead:
    """One Google Maps business listing."""

    company_name: str = ""
    website: str = ""
    phone: str = ""
    address: str = ""
    google_maps_url: str = ""
    category: str = ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the scraper."""
    parser = argparse.ArgumentParser(
        description="Scrape Google Maps listings for a keyword in a location.",
    )
    parser.add_argument("--keyword", required=True, help='e.g. "VFX studio"')
    parser.add_argument("--location", required=True, help='e.g. "Toronto"')
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Maximum number of listings to collect (default: 20).",
    )
    parser.add_argument(
        "--headless",
        type=_str_to_bool,
        default=True,
        help="Run browser headless (default: True). Pass False to see it.",
    )
    return parser.parse_args(argv)


def _str_to_bool(value: str) -> bool:
    """Parse truthy/falsy CLI strings like 'true', 'false', '1', '0'."""
    if isinstance(value, bool):
        return value
    if value.lower() in {"true", "1", "yes", "y", "t"}:
        return True
    if value.lower() in {"false", "0", "no", "n", "f"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean: {value!r}")


def polite_sleep(low: float = 1.5, high: float = 3.5) -> None:
    """Sleep a random interval to avoid hammering Google Maps."""
    time.sleep(random.uniform(low, high))


def build_search_url(keyword: str, location: str) -> str:
    """Build a Google Maps search URL for 'keyword in location'."""
    query = f"{keyword} in {location}".replace(" ", "+")
    return f"https://www.google.com/maps/search/{query}"


def _dismiss_consent(page: Page) -> None:
    """Click through Google's consent / cookie wall if it shows up."""
    selectors = [
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
        'button[aria-label="Accept all"]',
        'form[action*="consent"] button',
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                page.wait_for_timeout(800)
                return
        except Exception:
            continue


def _find_results_panel(page: Page):
    """Return the scrollable results-feed element, or None."""
    # Google Maps puts results in a role="feed" element.
    try:
        return page.query_selector('div[role="feed"]')
    except Exception:
        return None


def scroll_results(page: Page, max_results: int, feed_selector: str = 'div[role="feed"]') -> None:
    """Scroll the Google Maps results panel until enough cards are loaded.

    Stops when either ``max_results`` listings appear, the feed reports it
    has reached the end, or a safety cap of scroll attempts is hit.
    """
    last_count = 0
    stale_scrolls = 0
    max_scrolls = 40

    for _ in range(max_scrolls):
        cards = page.query_selector_all(f'{feed_selector} > div > div[jsaction]')
        count = len(cards)

        if count >= max_results:
            return

        # End-of-list marker Google Maps injects at the bottom.
        if page.query_selector('p.fontBodyMedium span:has-text("reached the end")'):
            return

        if count == last_count:
            stale_scrolls += 1
            if stale_scrolls >= 4:
                return
        else:
            stale_scrolls = 0

        last_count = count

        try:
            page.evaluate(
                "sel => { const el = document.querySelector(sel);"
                " if (el) el.scrollTop = el.scrollHeight; }",
                feed_selector,
            )
        except Exception:
            pass
        polite_sleep(1.5, 2.5)


def _text_or_empty(page: Page, selector: str) -> str:
    """Return trimmed text for the first matching selector, or ''."""
    try:
        el = page.query_selector(selector)
        if el:
            txt = el.inner_text().strip()
            return txt
    except Exception:
        pass
    return ""


def _attr_or_empty(page: Page, selector: str, attr: str) -> str:
    """Return an attribute of the first matching selector, or ''."""
    try:
        el = page.query_selector(selector)
        if el:
            val = el.get_attribute(attr)
            return (val or "").strip()
    except Exception:
        pass
    return ""


def extract_lead_from_detail(page: Page, maps_url: str) -> Lead:
    """Extract structured data from an open Google Maps detail panel."""
    lead = Lead(google_maps_url=maps_url)

    # Name lives in an h1 at the top of the detail panel.
    lead.company_name = _text_or_empty(page, "h1.DUwDvf") or _text_or_empty(page, "h1")

    # Category is in a button that opens a taxonomy picker.
    lead.category = _text_or_empty(page, "button[jsaction*='category']")

    # Website is a link with data-item-id="authority".
    lead.website = _attr_or_empty(page, 'a[data-item-id="authority"]', "href")
    if not lead.website:
        # Fallback: a link that has aria-label starting with "Website:".
        lead.website = _attr_or_empty(page, 'a[aria-label^="Website"]', "href")

    # Phone lives in a button with data-item-id starting with "phone:tel:".
    phone_el = page.query_selector('button[data-item-id^="phone:tel:"]')
    if phone_el:
        aria = (phone_el.get_attribute("aria-label") or "").strip()
        # aria looks like "Phone: 416-555-1234".
        lead.phone = aria.split(":", 1)[-1].strip() if aria else ""

    # Address is a button with data-item-id="address".
    addr_el = page.query_selector('button[data-item-id="address"]')
    if addr_el:
        aria = (addr_el.get_attribute("aria-label") or "").strip()
        lead.address = aria.replace("Address:", "").strip() if aria else ""

    return lead


def collect_result_cards(page: Page, max_results: int) -> list:
    """Return up to ``max_results`` clickable result-card handles."""
    feed = page.query_selector('div[role="feed"]')
    if feed is None:
        return []
    cards = feed.query_selector_all('a.hfpxzc')
    return cards[:max_results]


def scrape(keyword: str, location: str, max_results: int, headless: bool) -> list[Lead]:
    """Run a single scrape session and return a list of ``Lead`` objects."""
    leads: list[Lead] = []
    search_url = build_search_url(keyword, location)
    console.log(f"Opening {search_url}")

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()

        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            console.log("[yellow]Initial navigation timed out; continuing anyway.[/yellow]")

        _dismiss_consent(page)

        try:
            page.wait_for_selector('div[role="feed"]', timeout=15_000)
        except PlaywrightTimeoutError:
            console.log("[red]Results feed never appeared. Bailing out.[/red]")
            browser.close()
            return leads

        scroll_results(page, max_results=max_results)

        cards = collect_result_cards(page, max_results)
        console.log(f"Found {len(cards)} result cards")

        for idx, card in enumerate(cards, start=1):
            try:
                card.scroll_into_view_if_needed()
                card.click()
                page.wait_for_selector("h1", timeout=10_000)
                polite_sleep()
                maps_url = page.url
                lead = extract_lead_from_detail(page, maps_url)
                if lead.company_name:
                    leads.append(lead)
                    console.log(f"[green]✓[/green] ({idx}) {lead.company_name}")
                else:
                    console.log(f"[yellow]skip[/yellow] ({idx}) no name extracted")
            except Exception as exc:
                console.log(f"[red]✗[/red] ({idx}) {exc!s}")
                continue

        browser.close()
    return leads


def save_csv(leads: list[Lead], keyword: str, location: str) -> Path:
    """Write leads to a timestamped CSV in ``output/`` and return its path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_kw = keyword.replace(" ", "-").lower()
    safe_loc = location.replace(" ", "-").lower()
    path = OUTPUT_DIR / f"leads_{safe_kw}_{safe_loc}_{ts}.csv"

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for lead in leads:
            writer.writerow({k: getattr(lead, k, "") for k in FIELDS})
    return path


def print_table(leads: list[Lead]) -> None:
    """Pretty-print the collected leads as a rich table."""
    table = Table(title="Google Maps Leads", show_lines=False)
    for field_name in FIELDS:
        table.add_column(field_name.replace("_", " ").title(), overflow="fold")
    for lead in leads:
        table.add_row(*[getattr(lead, k, "") or "-" for k in FIELDS])
    console.print(table)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    try:
        leads = scrape(
            keyword=args.keyword,
            location=args.location,
            max_results=args.max_results,
            headless=args.headless,
        )
    except KeyboardInterrupt:
        console.log("[yellow]Interrupted by user.[/yellow]")
        return 130

    if not leads:
        console.log("[red]No leads collected.[/red]")
        return 1

    path = save_csv(leads, args.keyword, args.location)
    print_table(leads)
    console.log(f"[bold green]Saved {len(leads)} leads → {path}[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
