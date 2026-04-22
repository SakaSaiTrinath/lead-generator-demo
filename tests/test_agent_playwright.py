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

    def test_google_mode_unwraps_redirect_links(self, page):
        """Regression: real Google SERPs wrap results in /url?q=...

        Previously these were dropped because the host contains 'google.'
        leaving us with zero seeds when the agent fell back to a
        ``site:`` search.
        """
        # Use absolute hrefs so that page.eval_on_selector_all returns
        # the wrapped URL verbatim (instead of resolving /url?q=... against
        # about:blank, which would mangle the test).
        page.set_content(
            """
            <html><body>
              <a href="https://www.google.com/url?q=https://studio-one.example/&sa=U">One</a>
              <a href="https://www.google.com/url?q=https://studio-two.example/about&sa=U">Two</a>
              <a href="https://www.google.com/url?q=https://www.google.com/ads">unwrap-to-google</a>
              <a href="https://www.google.com/search?q=...">internal</a>
            </body></html>
            """
        )
        links = agent.collect_links(page, domain=None, google_mode=True)
        assert any(l.startswith("https://studio-one.example") for l in links)
        assert any(l.startswith("https://studio-two.example") for l in links)
        # An unwrap that itself points back at google.* is still dropped.
        assert not any("google.com" in l for l in links)


class TestCollectLinksFiltersSearchPages:
    def test_drops_search_path_links(self, page):
        """Regression: DDG/Google sometimes return YP's own search pages.

        Those pages produce junk "companies" like
        ``Animation Video To Image near Brampton ON (1 Result(s))`` when
        crawled, so they must be filtered out of the seed list.
        """
        page.set_content(
            """
            <html><body>
              <a href="https://www.yellowpages.ca/bus/ON/Toronto/Studio/1.html">Real</a>
              <a href="https://www.yellowpages.ca/search/si/1/animation/Toronto">Search</a>
              <a href="https://www.google.com/url?q=https://www.yellowpages.ca/search/si/2/x/y&sa=U">Wrapped search</a>
            </body></html>
            """
        )
        direct = agent.collect_links(page, domain="yellowpages.ca", google_mode=False)
        assert not any("/search/" in l for l in direct)

        via_serp = agent.collect_links(page, domain=None, google_mode=True)
        assert not any("/search/" in l for l in via_serp)


class TestHarvestEmailFromWebsite:
    def test_finds_mailto(self, browser, fixture_server):
        context = browser.new_context(locale="en-US")
        try:
            email = agent.harvest_email_from_website(
                context, f"{fixture_server}/business_home.html",
            )
        finally:
            context.close()
        assert email == "hello@pixelgrovefx.com"

    def test_returns_empty_when_no_email(self, browser, fixture_server):
        context = browser.new_context(locale="en-US")
        try:
            email = agent.harvest_email_from_website(
                context, f"{fixture_server}/business_no_email.html",
            )
        finally:
            context.close()
        assert email == ""

    def test_ignores_invalid_scheme(self, browser):
        context = browser.new_context()
        try:
            assert agent.harvest_email_from_website(context, "file:///etc") == ""
            assert agent.harvest_email_from_website(context, "garbage") == ""
        finally:
            context.close()


class TestHarvestContactFromWebsite:
    def test_returns_email_and_phone(self, browser, fixture_server):
        """Both fields populated when the site exposes mailto: AND tel:."""
        context = browser.new_context(locale="en-US")
        try:
            contact = agent.harvest_contact_from_website(
                context, f"{fixture_server}/business_home.html",
            )
        finally:
            context.close()
        assert contact.email == "hello@pixelgrovefx.com"
        assert contact.phone == "+14165167863"

    def test_empty_when_site_has_neither(self, browser, fixture_server):
        context = browser.new_context(locale="en-US")
        try:
            contact = agent.harvest_contact_from_website(
                context, f"{fixture_server}/business_no_email.html",
            )
        finally:
            context.close()
        assert contact.email == ""
        assert contact.phone == ""


class TestResolveRedirectUrl:
    def test_follows_meta_refresh(self, browser, fixture_server):
        """A YP-style redirect should resolve to the final destination URL.

        We pass an intentionally fake ``source_domain`` so the function
        treats the fixture server's host as off-domain and returns the URL.
        """
        context = browser.new_context(locale="en-US")
        try:
            final = agent.resolve_redirect_url(
                context,
                f"{fixture_server}/gourl/pixelgrove.html",
                source_domain="pretend-this-is-yp.test",
            )
        finally:
            context.close()
        assert final.endswith("/business_home.html")

    def test_returns_empty_when_redirect_lands_on_same_domain(
        self, browser, fixture_server,
    ):
        """If the redirect bounces back to the source domain, return ''.

        In production this means the directory's redirect didn't actually
        leave the directory — there's no real off-site URL to capture.
        """
        from urllib.parse import urlparse

        context = browser.new_context(locale="en-US")
        try:
            same_domain = urlparse(fixture_server).netloc
            final = agent.resolve_redirect_url(
                context,
                f"{fixture_server}/gourl/pixelgrove.html",
                source_domain=same_domain,
            )
        finally:
            context.close()
        assert final == ""


class TestScrapeProfilePageSkipsSearchPages:
    def test_search_url_returns_empty_lead(self, page, monkeypatch):
        """A /search/ URL must never be opened and must yield an empty lead."""
        monkeypatch.setattr(agent, "polite_sleep", lambda *a, **kw: None)
        lead = agent.scrape_profile_page(
            page,
            "https://yellowpages.ca/search/si/1/animation/Toronto",
        )
        assert lead.company_name == ""
        assert lead.source_url == "https://yellowpages.ca/search/si/1/animation/Toronto"


class TestUnwrapSearchRedirect:
    def test_unwraps_google_q_param(self):
        assert agent._unwrap_search_redirect(
            "https://www.google.com/url?q=https://studio.example/&sa=U&ved=abc"
        ) == "https://studio.example/"

    def test_unwraps_ddg_uddg_param(self):
        assert agent._unwrap_search_redirect(
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fstudio.example%2Fabout&rut=x"
        ) == "https://studio.example/about"

    def test_plain_http_passthrough(self):
        assert agent._unwrap_search_redirect(
            "https://studio.example/profile"
        ) == "https://studio.example/profile"

    def test_google_internal_dropped(self):
        assert agent._unwrap_search_redirect(
            "https://www.google.com/policies"
        ) is None

    def test_ddg_internal_dropped(self):
        assert agent._unwrap_search_redirect(
            "https://duckduckgo.com/settings"
        ) is None

    def test_wrapped_to_google_is_dropped(self):
        assert agent._unwrap_search_redirect(
            "https://www.google.com/url?q=https://www.google.com/ads"
        ) is None

    def test_wrapped_to_ddg_is_dropped(self):
        assert agent._unwrap_search_redirect(
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fsomewhere"
        ) is None

    def test_non_http_dropped(self):
        assert agent._unwrap_search_redirect("/relative") is None

    def test_backwards_compat_alias(self):
        # The old name still works so external callers don't break.
        assert agent._unwrap_google_redirect is agent._unwrap_search_redirect


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
