"""End-to-end tests that wire full pipelines against local HTTP fixtures.

These tests don't touch the real Google Maps / external sites. Instead
they drive the real functions with fixture URLs that simulate the DOM
each extractor expects.
"""

from __future__ import annotations

import csv

import pytest

import google_maps_scraper as gms
import lead_agent as agent


# ---------- Scraper pipeline (collect + extract + CSV) ----------

class TestScraperPipeline:
    def test_detail_page_to_csv(self, page, fixture_server, tmp_output_dir, monkeypatch):
        """Visit a fixture 'detail panel' and round-trip through save_csv."""
        monkeypatch.setattr(gms, "polite_sleep", lambda *a, **kw: None)

        url = f"{fixture_server}/gmaps_detail.html"
        page.goto(url)
        lead = gms.extract_lead_from_detail(page, url)

        path = gms.save_csv([lead], "vfx studio", "toronto")

        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))

        assert len(rows) == 1
        row = rows[0]
        assert row["company_name"] == "Pixel Grove VFX"
        assert row["website"] == "https://pixelgrovefx.com"
        assert row["phone"] == "+1 416-555-0134"
        assert row["address"].startswith("123 King St W")
        assert row["category"] == "Visual effects studio"
        assert row["google_maps_url"] == url


# ---------- Agent pipeline (search → crawl → CSV) ----------

class TestAgentPipeline:
    def test_directory_to_profile_to_csv(
        self, page, fixture_server, tmp_output_dir, monkeypatch,
    ):
        """Agent pipeline: collect seeds from index, crawl, save CSV."""
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)

        # Load the directory index to collect seed links.
        page.goto(f"{fixture_server}/directory_index.html")
        seeds = agent.collect_links(page, domain="127.0.0.1", google_mode=False)

        # Seeds from the fixture are relative (/profile/*). Only the
        # company_profile.html fixture is a real page, so we mock the
        # crawl to visit that page once.
        real_seed = f"{fixture_server}/company_profile.html"
        assert seeds, "directory fixture should expose at least one profile link"

        # Reuse the real crawler, but feed it the one seed we know is live.
        from rich.console import Console
        from rich.progress import Progress
        with Progress(console=Console(quiet=True)) as progress:
            task_id = progress.add_task("t", total=3)
            leads = agent.crawl(
                page=page,
                seed_urls=[real_seed],
                domain="127.0.0.1",
                max_results=3,
                depth=1,
                progress=progress,
                task_id=task_id,
            )

        assert len(leads) == 1
        path = agent.save_csv(leads, "clutch.co", "animation studio")

        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))

        assert len(rows) == 1
        row = rows[0]
        assert row["company_name"] == "Northern Lights Animation"
        assert row["email"] == "hello@northernlightsanim.com"
        assert row["website"] == "https://northernlightsanim.com"
        assert "Vancouver" in row["location"]
        assert "604-555-0198" in row["phone"]
        assert len(row["description"]) > 0
        assert row["source_url"] == real_seed

    def test_main_smoke_with_mocked_run(self, monkeypatch, tmp_output_dir):
        """CLI main() wires run() → save_csv() → exit 0 on success."""
        monkeypatch.setattr(
            agent, "run",
            lambda **kw: [agent.Lead(
                company_name="Smoke Studio",
                email="smoke@example.io",
                source_url="https://example.io/smoke",
            )],
        )
        rc = agent.main([
            "--keyword", "smoke",
            "--site", "example.io",
            "--max-results", "1",
        ])
        assert rc == 0
        csvs = sorted(tmp_output_dir.glob("agent_leads_*.csv"))
        assert len(csvs) == 1
        with csvs[0].open() as fh:
            data = fh.read()
        assert "Smoke Studio" in data
        assert "smoke@example.io" in data


# ---------- Pipeline resilience ----------

class TestPipelineResilience:
    def test_scrape_profile_page_timeout_returns_empty_lead(
        self, page, monkeypatch,
    ):
        """A page that fails to load should produce a Lead with just the URL."""
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)
        # Reserved TEST-NET-1 — guaranteed to not resolve to content.
        bad_url = "http://192.0.2.1:1/definitely-not-real"
        lead = agent.scrape_profile_page(page, bad_url)
        assert lead.source_url == bad_url
        assert lead.company_name == ""
        assert lead.email == ""
