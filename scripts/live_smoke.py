#!/usr/bin/env python3
"""Live end-to-end smoke test of both lead-gen tools.

Runs on the caller's real network (not a sandbox). Usage:

    python scripts/live_smoke.py                       # default small run
    python scripts/live_smoke.py --scraper-only
    python scripts/live_smoke.py --agent-only
    python scripts/live_smoke.py --headful             # watch the browser
    python scripts/live_smoke.py --keyword "VFX studio" --location "Toronto"

What it does:
  1. Pre-flight: Python 3.10+, required packages importable, Playwright
     chromium installed.
  2. Runs the Google Maps scraper against the given keyword+location with
     a small --max-results (default 3) so the test finishes in under a
     minute.
  3. Runs the agentic lead finder against a directory site (default
     yellowpages.ca, which usually works without CAPTCHA; clutch.co is
     stricter) with the same small cap.
  4. Validates that each tool produced a timestamped CSV, the expected
     columns are present, and at least one row was written.
  5. Prints a pass/fail summary plus a short human verification checklist.

Nothing here touches test fixtures — it's the real tools hitting real
sites. Expect occasional flakes from CAPTCHA, consent walls, or DOM
churn. Re-run with --headful to debug.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

SCRAPER_FIELDS = [
    "company_name", "website", "phone", "address",
    "google_maps_url", "category",
]
AGENT_FIELDS = [
    "company_name", "website", "email", "phone",
    "location", "description", "source_url",
]


# ---------- tiny ANSI helpers (no rich dep required for pre-flight) ----------

def _c(code: str, msg: str) -> str:
    if not sys.stdout.isatty():
        return msg
    return f"\033[{code}m{msg}\033[0m"


def ok(msg: str) -> None:    print(_c("32", "[ ok ]") + " " + msg)
def warn(msg: str) -> None:  print(_c("33", "[warn]") + " " + msg)
def fail(msg: str) -> None:  print(_c("31", "[fail]") + " " + msg)
def step(msg: str) -> None:  print(_c("36;1", "\n== " + msg + " =="))


# ---------- pre-flight ----------

def check_python() -> bool:
    need = (3, 10)
    have = sys.version_info[:2]
    if have >= need:
        ok(f"python {have[0]}.{have[1]} (>= 3.10)")
        return True
    fail(f"python {have[0]}.{have[1]} — need 3.10+")
    return False


def check_imports() -> bool:
    missing = []
    for mod in ("playwright", "rich"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        fail(f"missing packages: {', '.join(missing)}")
        print("  fix: pip install -r scraper/requirements.txt -r agent/requirements.txt")
        return False
    ok("playwright + rich importable")
    return True


def check_browser() -> bool:
    """Try launching a chromium to confirm the browser is installed."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
    except Exception as exc:
        fail(f"playwright chromium not usable: {exc}")
        print("  fix: python -m playwright install chromium")
        return False
    ok("playwright chromium launches")
    return True


# ---------- run a tool ----------

def _latest_csv(prefix: str, since: float) -> Path | None:
    """Return the newest CSV in output/ matching prefix that's newer than 'since'."""
    if not OUTPUT_DIR.exists():
        return None
    candidates = [
        p for p in OUTPUT_DIR.glob(f"{prefix}_*.csv")
        if p.stat().st_mtime >= since - 1
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _validate_csv(path: Path, expected_fields: list[str]) -> tuple[bool, int]:
    """Return (ok?, row_count) for a tool CSV."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)
    if header != expected_fields:
        fail(f"{path.name}: header mismatch")
        print(f"  got:      {header}")
        print(f"  expected: {expected_fields}")
        return False, len(rows)
    if not rows:
        warn(f"{path.name}: header OK but zero rows — target may have blocked us")
        return False, 0
    return True, len(rows)


def _run_cmd(argv: list[str], timeout: int) -> int:
    """Run a subprocess, stream its stdout, return the exit code."""
    print(_c("90", "$ " + " ".join(argv)))
    try:
        proc = subprocess.run(argv, cwd=ROOT, timeout=timeout, check=False)
        return proc.returncode
    except subprocess.TimeoutExpired:
        fail(f"command timed out after {timeout}s")
        return 124


def run_scraper(keyword: str, location: str, max_results: int, headless: bool) -> bool:
    step(f"Google Maps scraper: '{keyword}' in '{location}' (max {max_results})")
    started = time.time()
    rc = _run_cmd(
        [
            sys.executable, "scraper/google_maps_scraper.py",
            "--keyword", keyword,
            "--location", location,
            "--max-results", str(max_results),
            "--headless", "true" if headless else "false",
        ],
        timeout=300,
    )
    if rc != 0:
        fail(f"scraper exited with code {rc}")
        return False
    csv_path = _latest_csv("leads", started)
    if csv_path is None:
        fail("no leads_*.csv appeared in output/")
        return False
    ok_, count = _validate_csv(csv_path, SCRAPER_FIELDS)
    if ok_:
        ok(f"{csv_path.name} — {count} leads")
    return ok_


def run_agent(keyword: str, site: str, max_results: int, depth: int, headless: bool) -> bool:
    step(f"Agent: '{keyword}' on {site} (max {max_results}, depth {depth})")
    started = time.time()
    rc = _run_cmd(
        [
            sys.executable, "agent/lead_agent.py",
            "--keyword", keyword,
            "--site", site,
            "--max-results", str(max_results),
            "--depth", str(depth),
            "--headless", "true" if headless else "false",
        ],
        timeout=600,
    )
    if rc != 0:
        fail(f"agent exited with code {rc}")
        return False
    csv_path = _latest_csv("agent_leads", started)
    if csv_path is None:
        fail("no agent_leads_*.csv appeared in output/")
        return False
    ok_, count = _validate_csv(csv_path, AGENT_FIELDS)
    if ok_:
        ok(f"{csv_path.name} — {count} leads")
    return ok_


# ---------- checklist ----------

CHECKLIST = """
  [ ] The scraper CSV has a real company_name for every row
  [ ] At least one row has a website, phone, or address populated
  [ ] google_maps_url points at a real /maps/place/ link
  [ ] The agent CSV has at least one row with an email (or explain why not)
  [ ] Descriptions look like real about-text, not nav/footer noise
  [ ] Re-running with a different --location yields different companies
"""


def print_checklist() -> None:
    step("Human verification checklist")
    print(_c("90", CHECKLIST.strip("\n")))


# ---------- entry point ----------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live smoke test for both lead-gen tools.")
    p.add_argument("--keyword", default="VFX studio",
                   help='Scraper keyword (default: "VFX studio")')
    p.add_argument("--location", default="Toronto",
                   help='Scraper location (default: "Toronto")')
    p.add_argument("--agent-keyword", default="animation studio",
                   help='Agent keyword (default: "animation studio")')
    p.add_argument("--site", default="yellowpages.ca",
                   help='Agent target site (default: "yellowpages.ca")')
    p.add_argument("--max-results", type=int, default=3,
                   help="Cap per tool (default: 3, keeps the smoke <1 min each)")
    p.add_argument("--depth", type=int, default=2, choices=[1, 2, 3],
                   help="Agent crawl depth (default: 2)")
    p.add_argument("--scraper-only", action="store_true")
    p.add_argument("--agent-only", action="store_true")
    p.add_argument("--headful", action="store_true",
                   help="Show the browser (helpful for debugging CAPTCHAs)")
    p.add_argument("--skip-preflight", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    headless = not args.headful
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_preflight:
        step("pre-flight")
        if not (check_python() and check_imports() and check_browser()):
            fail("pre-flight failed — fix the above and re-run")
            return 2

    results: list[tuple[str, bool]] = []

    if not args.agent_only:
        results.append((
            "scraper",
            run_scraper(args.keyword, args.location, args.max_results, headless),
        ))
    if not args.scraper_only:
        results.append((
            "agent",
            run_agent(args.agent_keyword, args.site, args.max_results, args.depth, headless),
        ))

    step("summary")
    for name, passed in results:
        (ok if passed else fail)(f"{name}: {'passed' if passed else 'FAILED'}")

    print_checklist()

    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    sys.exit(main())
