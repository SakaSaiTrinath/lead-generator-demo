"""Agentic lead finder that crawls a target site up to N levels deep.

The agent:
  1. Opens ``--site`` in a headless Chromium browser.
  2. Looks for a search input on the site and submits ``--keyword``.
     If nothing search-like is found, it falls back to a Google
     ``site:<domain> <keyword>`` query and scrapes those results instead.
  3. Follows links that look like company-profile pages up to ``--depth``
     levels (max 3) and scrapes each page for name/website/email/phone/
     location/description.
  4. Streams progress through a rich progress bar and writes a timestamped
     CSV under ``output/``.

Run:
    python lead_agent.py --keyword "animation studio" --site "clutch.co"
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from playwright.sync_api import (
    Browser,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


# Project root (parent of agent/) — CSVs land in <root>/output/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"

FIELDS = [
    "company_name",
    "website",
    "email",
    "phone",
    "location",
    "description",
    "source_url",
]

# Robust enough for typical cold-email targets; ignores obvious junk prefixes.
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{2,4}\)?[\s\-.]?)?\d{3}[\s\-.]?\d{3,4}"
)
# Phrases on links that typically indicate a company-profile page.
PROFILE_LINK_HINTS = (
    "/profile/",
    "/company/",
    "/companies/",
    "/business/",
    "/listing/",
    "/agency/",
    "/studio/",
    "/vendor/",
    "/provider/",
    "/supplier/",
    "/firms/",
    "/agencies/",
)
# Emails we never want to capture.
EMAIL_BLOCKLIST = (
    "sentry.io",
    "wixpress.com",
    "example.com",
    "example.org",
    "domain.com",
    "email.com",
    ".png",
    ".jpg",
    ".gif",
    ".svg",
)

console = Console()


@dataclass
class Lead:
    """A single extracted lead record."""

    company_name: str = ""
    website: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    description: str = ""
    source_url: str = ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the agent."""
    parser = argparse.ArgumentParser(
        description="Agentic lead finder that crawls a target site for companies.",
    )
    parser.add_argument("--keyword", required=True, help='e.g. "animation studio"')
    parser.add_argument(
        "--site",
        required=True,
        help='Domain to crawl (e.g. "clutch.co" or "yellowpages.ca")',
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Maximum leads to collect (default: 20).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Crawl depth. 1 = search page only. 3 = follow profile sub-pages.",
    )
    parser.add_argument(
        "--headless",
        type=_str_to_bool,
        default=True,
        help="Run browser headless (default: True).",
    )
    return parser.parse_args(argv)


def _str_to_bool(value: str) -> bool:
    """Parse truthy/falsy CLI strings."""
    if isinstance(value, bool):
        return value
    if value.lower() in {"true", "1", "yes", "y", "t"}:
        return True
    if value.lower() in {"false", "0", "no", "n", "f"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean: {value!r}")


def polite_sleep(low: float = 1.5, high: float = 3.5) -> None:
    """Random-interval sleep between requests to avoid rate limiting."""
    time.sleep(random.uniform(low, high))


def normalize_site(site: str) -> tuple[str, str]:
    """Return (root_url, bare_domain) for user input like 'clutch.co'."""
    site = site.strip()
    if not site.startswith("http"):
        site = "https://" + site
    parsed = urlparse(site)
    domain = parsed.netloc or parsed.path
    return f"{parsed.scheme or 'https'}://{domain}", domain


def try_site_search(page: Page, root_url: str, keyword: str) -> bool:
    """Try to find and submit a search input on the site's homepage.

    Returns True if a search appeared to succeed (URL or DOM changed).
    """
    try:
        page.goto(root_url, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        return False

    polite_sleep(1.5, 2.5)

    candidate_selectors = [
        'input[type="search"]',
        'input[name*="search" i]',
        'input[placeholder*="search" i]',
        'input[aria-label*="search" i]',
        'input[id*="search" i]',
        'input[name="q"]',
    ]

    search_box = None
    for sel in candidate_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                search_box = el
                break
        except Exception:
            continue

    if search_box is None:
        return False

    try:
        start_url = page.url
        search_box.click()
        search_box.fill(keyword)
        search_box.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
        polite_sleep(2, 3)
        return page.url != start_url
    except Exception:
        return False


def google_site_search(page: Page, domain: str, keyword: str) -> bool:
    """Fallback: run a Google ``site:<domain> <keyword>`` search."""
    query = f"site:{domain} {keyword}"
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        return False
    _maybe_accept_google_consent(page)
    polite_sleep(1.5, 2.5)
    return True


def _maybe_accept_google_consent(page: Page) -> None:
    """Click past Google's consent wall if it's shown."""
    for sel in (
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
        'form[action*="consent"] button',
    ):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(600)
                return
        except Exception:
            continue


def collect_links(page: Page, domain: str | None, google_mode: bool) -> list[str]:
    """Collect outgoing links from the current page.

    When ``google_mode`` is True, return the organic result hrefs. Otherwise
    return links whose path hints at a company profile.
    """
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href)",
        )
    except Exception:
        hrefs = []

    out: list[str] = []
    seen: set[str] = set()

    for href in hrefs:
        if not href or href in seen:
            continue
        seen.add(href)
        low = href.lower()
        if google_mode:
            # Filter to external result URLs; skip Google-internal links.
            if "google." in urlparse(href).netloc:
                continue
            if href.startswith("http"):
                out.append(href)
        else:
            parsed = urlparse(href)
            if domain and parsed.netloc and domain not in parsed.netloc:
                continue
            if any(hint in low for hint in PROFILE_LINK_HINTS):
                out.append(href)
    return out


def _clean_email(raw: str) -> str:
    """Filter obviously-bad emails (tracking, image file names, etc.)."""
    low = raw.lower()
    if any(bad in low for bad in EMAIL_BLOCKLIST):
        return ""
    return raw


def extract_emails(text: str) -> str:
    """Return the first credible email in ``text``, or ''."""
    for match in EMAIL_RE.findall(text):
        cleaned = _clean_email(match)
        if cleaned:
            return cleaned
    return ""


def extract_phone(text: str) -> str:
    """Return the first plausible phone number in ``text``, or ''."""
    for match in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if 7 <= len(digits) <= 15:
            return match.strip()
    return ""


def extract_description(page: Page) -> str:
    """Return the first 200 chars of the page's about/description text."""
    for sel in (
        'meta[name="description"]',
        'meta[property="og:description"]',
    ):
        try:
            el = page.query_selector(sel)
            if el:
                content = (el.get_attribute("content") or "").strip()
                if content:
                    return content[:200]
        except Exception:
            continue

    for sel in (
        "section#about",
        "div.about",
        'section:has(h2:has-text("About"))',
        "p",
    ):
        try:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip()
                if len(txt) > 30:
                    return txt[:200]
        except Exception:
            continue
    return ""


def extract_company_name(page: Page) -> str:
    """Best-effort company name extraction from a profile page."""
    for sel in ("h1", 'meta[property="og:site_name"]', "title"):
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            if sel.startswith("meta"):
                val = (el.get_attribute("content") or "").strip()
            else:
                val = el.inner_text().strip()
            if val:
                return val[:120]
        except Exception:
            continue
    return ""


def extract_website(page: Page, current_url: str) -> str:
    """Find the 'visit website' link on a profile page, if present."""
    current_domain = urlparse(current_url).netloc
    candidate_selectors = [
        'a:has-text("Visit website")',
        'a:has-text("Website")',
        'a[aria-label*="website" i]',
        'a[rel*="nofollow"]',
    ]
    for sel in candidate_selectors:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            href = (el.get_attribute("href") or "").strip()
            if not href:
                continue
            full = urljoin(current_url, href)
            host = urlparse(full).netloc
            # Website links point off-domain.
            if host and host != current_domain:
                return full
        except Exception:
            continue
    return ""


def scrape_profile_page(page: Page, url: str) -> Lead:
    """Visit ``url`` and return a populated ``Lead``.

    Any navigation error (timeout, DNS failure, unsafe port, connection
    refused, etc.) yields a Lead with just ``source_url`` set so the
    caller's crawl loop can move on.
    """
    lead = Lead(source_url=url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        return lead
    polite_sleep(1.5, 2.5)

    try:
        body_text = page.inner_text("body")
    except Exception:
        body_text = ""

    lead.company_name = extract_company_name(page)
    lead.website = extract_website(page, url)
    lead.email = extract_emails(body_text)
    lead.phone = extract_phone(body_text)
    lead.description = extract_description(page)

    # Location: search for common address-like snippets.
    loc = ""
    for sel in (
        '[itemprop="address"]',
        "address",
        '[class*="address" i]',
        '[class*="location" i]',
    ):
        try:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip()
                if txt:
                    loc = txt[:200]
                    break
        except Exception:
            continue
    lead.location = loc
    return lead


def crawl(
    page: Page,
    seed_urls: list[str],
    domain: str,
    max_results: int,
    depth: int,
    progress: Progress,
    task_id,
) -> list[Lead]:
    """BFS-crawl starting from ``seed_urls`` up to ``depth`` levels deep."""
    leads: list[Lead] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(u, 1) for u in seed_urls]

    while queue and len(leads) < max_results:
        url, level = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)

        lead = scrape_profile_page(page, url)
        if lead.company_name:
            leads.append(lead)
            progress.update(
                task_id,
                advance=1,
                description=f"[cyan]crawling[/cyan] {lead.company_name[:40]}",
            )

        if level < depth and len(leads) < max_results:
            sub_links = collect_links(page, domain=domain, google_mode=False)
            for sub in sub_links:
                if sub not in seen:
                    queue.append((sub, level + 1))
    return leads


def run(
    keyword: str,
    site: str,
    max_results: int,
    depth: int,
    headless: bool,
) -> list[Lead]:
    """Run the full agent pipeline and return collected leads."""
    root_url, domain = normalize_site(site)
    leads: list[Lead] = []

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

        console.log(f"[bold]Step 1[/bold] trying on-site search at {root_url}")
        google_mode = False
        if not try_site_search(page, root_url, keyword):
            console.log(
                "[yellow]No usable search input — falling back to Google site: search.[/yellow]"
            )
            if not google_site_search(page, domain, keyword):
                console.log("[red]Google fallback failed. Nothing to crawl.[/red]")
                browser.close()
                return leads
            google_mode = True

        console.log("[bold]Step 2[/bold] collecting seed links")
        seed_links = collect_links(
            page,
            domain=None if google_mode else domain,
            google_mode=google_mode,
        )
        # De-dupe while preserving order.
        seen = set()
        dedup_seeds: list[str] = []
        for link in seed_links:
            if link not in seen:
                seen.add(link)
                dedup_seeds.append(link)
        # Cap seeds to a reasonable multiple of max_results.
        dedup_seeds = dedup_seeds[: max_results * 3 or 60]
        console.log(f"[bold]Step 3[/bold] crawling {len(dedup_seeds)} seed URLs, depth={depth}")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            task_id = progress.add_task("[cyan]crawling[/cyan]", total=max_results)
            leads = crawl(
                page=page,
                seed_urls=dedup_seeds,
                domain=domain,
                max_results=max_results,
                depth=depth,
                progress=progress,
                task_id=task_id,
            )

        browser.close()
    return leads


def save_csv(leads: list[Lead], site: str, keyword: str) -> Path:
    """Write leads to a timestamped CSV under ``output/``."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_site = re.sub(r"[^a-z0-9]+", "-", site.lower()).strip("-")
    safe_kw = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    path = OUTPUT_DIR / f"agent_leads_{safe_site}_{safe_kw}_{ts}.csv"

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for lead in leads:
            writer.writerow({k: getattr(lead, k, "") for k in FIELDS})
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)

    try:
        leads = run(
            keyword=args.keyword,
            site=args.site,
            max_results=args.max_results,
            depth=args.depth,
            headless=args.headless,
        )
    except KeyboardInterrupt:
        console.log("[yellow]Interrupted by user.[/yellow]")
        return 130

    if not leads:
        console.log("[red]No leads collected.[/red]")
        return 1

    path = save_csv(leads, args.site, args.keyword)
    console.log(f"[bold green]Saved {len(leads)} leads → {path}[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
