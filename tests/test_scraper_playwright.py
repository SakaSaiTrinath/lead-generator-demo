"""Playwright integration tests for scraper/google_maps_scraper.py.

Loads local HTML fixtures that mimic Google Maps markup and exercises the
real DOM-extraction functions against them.
"""

from __future__ import annotations

import pytest

import google_maps_scraper as gms


# ---------- extract_lead_from_detail ----------

class TestExtractLeadFromDetail:
    def test_full_detail_panel(self, page, fixture_server):
        url = f"{fixture_server}/gmaps_detail.html"
        page.goto(url)

        lead = gms.extract_lead_from_detail(page, url)

        assert lead.company_name == "Pixel Grove VFX"
        assert lead.category == "Visual effects studio"
        assert lead.website == "https://pixelgrovefx.com"
        assert lead.phone == "+1 416-555-0134"
        assert lead.address == "123 King St W, Toronto, ON M5V 1J2"
        assert lead.google_maps_url == url

    def test_minimal_panel_no_crash(self, page, fixture_server):
        """Missing fields should serialize as empty strings, not raise."""
        url = f"{fixture_server}/gmaps_detail_minimal.html"
        page.goto(url)

        lead = gms.extract_lead_from_detail(page, url)

        assert lead.company_name == "Minimal Studio"
        assert lead.website == ""
        assert lead.phone == ""
        assert lead.address == ""
        assert lead.category == ""

    def test_results_sidebar_not_treated_as_company(self, page, fixture_server):
        """Regression: a bare <h1>Results</h1> sidebar must not yield a lead.

        Earlier versions fell back to the generic <h1> selector and
        recorded a phantom "Results" row whenever Google's client-side
        navigation hadn't settled before we read the DOM.
        """
        url = f"{fixture_server}/gmaps_results_sidebar.html"
        page.goto(url)
        lead = gms.extract_lead_from_detail(page, url)
        assert lead.company_name == ""


# ---------- collect_result_cards / feed scrolling ----------

class TestCollectResultCards:
    def test_returns_at_most_max_results(self, page, fixture_server):
        page.goto(f"{fixture_server}/gmaps_feed.html")
        cards = gms.collect_result_cards(page, max_results=3)
        assert len(cards) == 3

    def test_returns_all_when_fewer_than_max(self, page, fixture_server):
        page.goto(f"{fixture_server}/gmaps_feed.html")
        cards = gms.collect_result_cards(page, max_results=100)
        assert len(cards) == 5

    def test_empty_page_returns_empty_list(self, page):
        page.set_content("<html><body><p>nothing here</p></body></html>")
        cards = gms.collect_result_cards(page, max_results=10)
        assert cards == []


class TestScrollResults:
    def test_exits_when_end_marker_present(self, page, fixture_server, monkeypatch):
        """End-of-list marker should short-circuit the scroll loop."""
        # Remove sleep — we don't want to actually wait 1.5s+ per iteration.
        monkeypatch.setattr(gms, "polite_sleep", lambda *a, **kw: None)
        page.goto(f"{fixture_server}/gmaps_feed.html")
        # Should return quickly without raising.
        gms.scroll_results(page, max_results=50)


# ---------- consent dismissal ----------

class TestHarvestEmailFromWebsite:
    def test_finds_mailto_link(self, browser, fixture_server):
        context = browser.new_context(locale="en-US")
        try:
            email = gms.harvest_email_from_website(
                context, f"{fixture_server}/business_home.html",
            )
        finally:
            context.close()
        assert email == "hello@pixelgrovefx.com"

    def test_returns_empty_when_no_email(self, browser, fixture_server):
        context = browser.new_context(locale="en-US")
        try:
            email = gms.harvest_email_from_website(
                context, f"{fixture_server}/business_no_email.html",
            )
        finally:
            context.close()
        assert email == ""

    def test_ignores_invalid_scheme(self, browser):
        context = browser.new_context()
        try:
            assert gms.harvest_email_from_website(context, "file:///etc/passwd") == ""
            assert gms.harvest_email_from_website(context, "not a url") == ""
        finally:
            context.close()


class TestDismissConsent:
    def test_clicks_accept_button_when_present(self, page):
        page.set_content(
            """
            <html><body>
              <button id="accept">Accept all</button>
              <p id="after">loaded</p>
            </body></html>
            """
        )
        # Patch wait_for_timeout to avoid the 800ms sleep slowing tests.
        original_wait = page.wait_for_timeout
        page.wait_for_timeout = lambda *a, **kw: None  # type: ignore[method-assign]
        try:
            gms._dismiss_consent(page)
        finally:
            page.wait_for_timeout = original_wait  # type: ignore[method-assign]

    def test_no_button_is_noop(self, page):
        page.set_content("<html><body><p>no consent wall</p></body></html>")
        gms._dismiss_consent(page)  # should not raise
