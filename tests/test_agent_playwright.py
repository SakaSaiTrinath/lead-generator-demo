"""Playwright integration tests for agent/lead_agent.py.

Loads local fixtures representing a directory index + a company profile
page, and exercises the real extraction and crawl functions end-to-end.
"""

from __future__ import annotations

import pytest

import lead_agent as agent


# ---------- extract_company_name / description / website ----------

class TestProfileExtraction:
    def test_company_name_from_h1(self, page, fixture_server):
        page.goto(f"{fixture_server}/company_profile.html")
        assert agent.extract_company_name(page) == "Northern Lights Animation"

    def test_description_from_meta(self, page, fixture_server):
        page.goto(f"{fixture_server}/company_profile.html")
        desc = agent.extract_description(page)
        assert "Boutique 2D/3D animation studio" in desc
        assert len(desc) <= 200

    def test_website_link_extracted(self, page, fixture_server):
        url = f"{fixture_server}/company_profile.html"
        page.goto(url)
        website = agent.extract_website(page, url)
        assert website == "https://northernlightsanim.com"


# ---------- scrape_profile_page (full lead) ----------

class TestScrapeProfilePage:
    def test_end_to_end_fields(self, page, fixture_server, monkeypatch):
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)
        url = f"{fixture_server}/company_profile.html"
        lead = agent.scrape_profile_page(page, url)

        assert lead.company_name == "Northern Lights Animation"
        assert lead.email == "hello@northernlightsanim.com"
        assert "604-555-0198" in lead.phone
        assert "Vancouver" in lead.location
        assert "Boutique 2D/3D animation studio" in lead.description
        assert lead.website == "https://northernlightsanim.com"
        assert lead.source_url == url

    def test_missing_page_does_not_crash(self, page, fixture_server, monkeypatch):
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)
        # URL that will 404 against the fixture server.
        url = f"{fixture_server}/does_not_exist_404.html"
        lead = agent.scrape_profile_page(page, url)
        # Should still return a Lead with source_url set.
        assert lead.source_url == url


# ---------- collect_links ----------

class TestCollectLinks:
    def test_filters_to_profile_links(self, page, fixture_server):
        page.goto(f"{fixture_server}/directory_index.html")
        links = agent.collect_links(page, domain="127.0.0.1", google_mode=False)
        joined = " ".join(links)
        assert "/profile/northern-lights" in joined
        assert "/profile/pixel-grove" in joined
        assert "/company/aurora-motion" in joined
        # Non-profile page filtered out.
        assert "/about" not in joined
        # Off-domain link filtered out.
        assert "example.com/external" not in joined

    def test_google_mode_returns_external_http_links(self, page):
        page.set_content(
            """
            <html><body>
              <a href="https://studio-one.example">One</a>
              <a href="https://studio-two.example">Two</a>
              <a href="https://www.google.com/policies">google internal</a>
              <a href="/relative">rel</a>
            </body></html>
            """
        )
        links = agent.collect_links(page, domain=None, google_mode=True)
        # Chromium normalizes URLs with a trailing slash.
        assert any(l.startswith("https://studio-one.example") for l in links)
        assert any(l.startswith("https://studio-two.example") for l in links)
        assert not any("google.com" in l for l in links)


# ---------- try_site_search (real DOM) ----------

class TestTrySiteSearch:
    def test_finds_and_submits_search_input(self, page, fixture_server, monkeypatch):
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)
        # Fixture has a visible <input type="search"> whose submission reloads
        # with ?q=... appended — that counts as a URL change.
        result = agent.try_site_search(
            page,
            root_url=f"{fixture_server}/directory_index.html",
            keyword="animation",
        )
        assert result is True
        # URL should contain the submitted query parameter.
        assert "q=animation" in page.url

    def test_returns_false_when_no_search_input(self, page, fixture_server, monkeypatch):
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)
        result = agent.try_site_search(
            page,
            root_url=f"{fixture_server}/company_profile.html",
            keyword="anything",
        )
        assert result is False


# ---------- crawl (BFS w/ real browser) ----------

class TestCrawl:
    def test_crawl_with_single_seed_fills_one_lead(self, page, fixture_server, monkeypatch):
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)

        from rich.console import Console
        from rich.progress import Progress
        # Use a quiet in-memory progress so tests don't write to stdout.
        with Progress(console=Console(quiet=True)) as progress:
            task_id = progress.add_task("t", total=5)
            leads = agent.crawl(
                page=page,
                seed_urls=[f"{fixture_server}/company_profile.html"],
                domain="127.0.0.1",
                max_results=5,
                depth=1,
                progress=progress,
                task_id=task_id,
            )
        assert len(leads) == 1
        assert leads[0].company_name == "Northern Lights Animation"
        assert leads[0].email == "hello@northernlightsanim.com"
