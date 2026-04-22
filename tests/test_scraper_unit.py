"""Unit tests for scraper/google_maps_scraper.py helpers (no browser)."""

from __future__ import annotations

import argparse
import csv
import time

import pytest

import google_maps_scraper as gms


# ---------- CLI / argparse ----------

class TestParseArgs:
    def test_defaults(self):
        ns = gms.parse_args(["--keyword", "VFX", "--location", "Toronto"])
        assert ns.keyword == "VFX"
        assert ns.location == "Toronto"
        assert ns.max_results == 20
        assert ns.headless is True

    def test_overrides(self):
        ns = gms.parse_args([
            "--keyword", "film",
            "--location", "Berlin",
            "--max-results", "7",
            "--headless", "false",
        ])
        assert ns.max_results == 7
        assert ns.headless is False

    def test_missing_required_fails(self):
        with pytest.raises(SystemExit):
            gms.parse_args(["--keyword", "only"])

    @pytest.mark.parametrize("val", ["yes", "Y", "1", "true", "T"])
    def test_truthy_bool(self, val):
        assert gms._str_to_bool(val) is True

    @pytest.mark.parametrize("val", ["no", "N", "0", "false", "F"])
    def test_falsy_bool(self, val):
        assert gms._str_to_bool(val) is False

    def test_bool_passthrough(self):
        assert gms._str_to_bool(True) is True
        assert gms._str_to_bool(False) is False

    def test_bool_invalid(self):
        with pytest.raises(argparse.ArgumentTypeError):
            gms._str_to_bool("maybe")


# ---------- URL building ----------

class TestBuildSearchUrl:
    def test_simple(self):
        url = gms.build_search_url("VFX studio", "Toronto")
        assert url == "https://www.google.com/maps/search/VFX+studio+in+Toronto"

    def test_multi_word_location(self):
        url = gms.build_search_url("film production", "New York")
        assert url == "https://www.google.com/maps/search/film+production+in+New+York"


# ---------- Dataclass ----------

class TestLead:
    def test_defaults_are_empty_strings(self):
        lead = gms.Lead()
        for field_name in gms.FIELDS:
            assert getattr(lead, field_name) == ""

    def test_fields_constant_order(self):
        assert gms.FIELDS == [
            "company_name",
            "website",
            "email",
            "phone",
            "address",
            "google_maps_url",
            "category",
        ]


# ---------- email helpers ----------

class TestEmailHelpers:
    def test_clean_email_strips_blocklisted(self):
        assert gms._clean_email("foo@example.com") == ""
        assert gms._clean_email("logo@2x.png") == ""
        assert gms._clean_email("hi@realstudio.com") == "hi@realstudio.com"

    def test_extract_email_from_text_returns_first_credible(self):
        text = "Contact logo@2x.png first, then hello@studio.io."
        assert gms._extract_email_from_text(text) == "hello@studio.io"

    def test_extract_email_from_text_none(self):
        assert gms._extract_email_from_text("no email here") == ""


# ---------- Polite sleep ----------

class TestPoliteSleep:
    def test_sleeps_within_bounds(self, monkeypatch):
        captured = {}

        def fake_sleep(seconds):
            captured["seconds"] = seconds

        monkeypatch.setattr(time, "sleep", fake_sleep)
        gms.polite_sleep(1.5, 3.5)
        assert 1.5 <= captured["seconds"] <= 3.5

    def test_custom_range(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(time, "sleep", lambda s: captured.update(seconds=s))
        gms.polite_sleep(0.1, 0.2)
        assert 0.1 <= captured["seconds"] <= 0.2


# ---------- CSV output ----------

class TestSaveCsv:
    def test_writes_header_and_rows(self, tmp_output_dir):
        leads = [
            gms.Lead(
                company_name="Pixel Grove VFX",
                website="https://pixelgrovefx.com",
                phone="+1 416-555-0134",
                address="123 King St W, Toronto",
                google_maps_url="https://maps.google.com/?cid=1",
                category="Visual effects studio",
            ),
            gms.Lead(company_name="Empty Fields Studio"),
        ]
        path = gms.save_csv(leads, "vfx", "toronto")

        assert path.exists()
        assert path.parent == tmp_output_dir
        assert path.name.startswith("leads_vfx_toronto_")
        assert path.name.endswith(".csv")

        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert [r["company_name"] for r in rows] == ["Pixel Grove VFX", "Empty Fields Studio"]
        assert rows[0]["website"] == "https://pixelgrovefx.com"
        # Missing fields serialize as empty strings, not "None".
        assert rows[1]["website"] == ""
        assert rows[1]["phone"] == ""

    def test_filename_is_slugified(self, tmp_output_dir):
        path = gms.save_csv([gms.Lead(company_name="x")], "Film Production", "New York")
        assert "film-production" in path.name.lower()
        assert "new-york" in path.name.lower()

    def test_empty_leads_still_writes_header(self, tmp_output_dir):
        path = gms.save_csv([], "kw", "loc")
        with path.open() as fh:
            first = fh.readline().strip().split(",")
        assert first == gms.FIELDS


# ---------- main() behavior when nothing is scraped ----------

class TestMainNoLeads:
    def test_returns_1_when_scrape_returns_empty(self, monkeypatch, tmp_output_dir):
        monkeypatch.setattr(gms, "scrape", lambda **kw: [])
        rc = gms.main(["--keyword", "x", "--location", "y"])
        assert rc == 1

    def test_returns_0_and_writes_csv_when_leads_present(self, monkeypatch, tmp_output_dir):
        monkeypatch.setattr(
            gms,
            "scrape",
            lambda **kw: [gms.Lead(company_name="OnlyOne")],
        )
        rc = gms.main(["--keyword", "x", "--location", "y"])
        assert rc == 0
        csvs = list(tmp_output_dir.glob("leads_*.csv"))
        assert len(csvs) == 1
