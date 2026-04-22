"""Unit tests for agent/lead_agent.py helpers (no browser)."""

from __future__ import annotations

import argparse
import csv
import time

import pytest

import lead_agent as agent


# ---------- CLI / argparse ----------

class TestParseArgs:
    def test_defaults(self):
        ns = agent.parse_args(["--keyword", "animation", "--site", "clutch.co"])
        assert ns.keyword == "animation"
        assert ns.site == "clutch.co"
        assert ns.max_results == 20
        assert ns.depth == 2
        assert ns.headless is True

    def test_depth_capped_to_3(self):
        with pytest.raises(SystemExit):
            agent.parse_args([
                "--keyword", "k", "--site", "s.com", "--depth", "4",
            ])

    def test_depth_minimum_1(self):
        ns = agent.parse_args([
            "--keyword", "k", "--site", "s.com", "--depth", "1",
        ])
        assert ns.depth == 1

    def test_missing_required_fails(self):
        with pytest.raises(SystemExit):
            agent.parse_args(["--keyword", "k"])

    def test_bool_invalid(self):
        with pytest.raises(argparse.ArgumentTypeError):
            agent._str_to_bool("nope-maybe")


# ---------- normalize_site ----------

class TestNormalizeSite:
    @pytest.mark.parametrize(
        "raw, expected_root, expected_domain",
        [
            ("clutch.co", "https://clutch.co", "clutch.co"),
            ("https://clutch.co", "https://clutch.co", "clutch.co"),
            ("https://clutch.co/path", "https://clutch.co", "clutch.co"),
            ("http://yellowpages.ca", "http://yellowpages.ca", "yellowpages.ca"),
            ("   clutch.co  ", "https://clutch.co", "clutch.co"),
        ],
    )
    def test_variants(self, raw, expected_root, expected_domain):
        root, domain = agent.normalize_site(raw)
        assert root == expected_root
        assert domain == expected_domain


# ---------- Email regex ----------

class TestExtractEmails:
    def test_finds_simple(self):
        assert agent.extract_emails("Contact: hello@northernlightsanim.com for info") == \
               "hello@northernlightsanim.com"

    def test_plus_and_dots(self):
        assert agent.extract_emails("sales+leads@my.studio.io ping") == \
               "sales+leads@my.studio.io"

    def test_returns_empty_when_none(self):
        assert agent.extract_emails("no email here") == ""

    def test_blocklist_skipped(self):
        # First match is a tracking address; second is the real one.
        text = "abc@sentry.io and real@studio.com"
        assert agent.extract_emails(text) == "real@studio.com"

    def test_image_filename_skipped(self):
        text = "logo@2x.png comes before reach@firm.com"
        assert agent.extract_emails(text) == "reach@firm.com"

    def test_clean_email_blocklist(self):
        assert agent._clean_email("abc@example.com") == ""
        assert agent._clean_email("abc@realdomain.io") == "abc@realdomain.io"


# ---------- Phone regex ----------

class TestExtractPhone:
    @pytest.mark.parametrize(
        "text, expected_substr",
        [
            ("Phone: +1 416-555-0134.", "416-555-0134"),
            ("Call (604) 555 0198 now", "555 0198"),
            ("tel: +44 20 7946 0018 info", "7946"),
        ],
    )
    def test_matches(self, text, expected_substr):
        out = agent.extract_phone(text)
        assert expected_substr in out

    def test_no_match(self):
        assert agent.extract_phone("no digits at all") == ""

    def test_rejects_short_runs(self):
        # Fewer than 7 digits — should not be returned.
        assert agent.extract_phone("code 123 456") == "" or \
               len(agent.extract_phone("code 123 456").replace(" ", "").replace("-", "")) >= 7


# ---------- Lead dataclass / FIELDS ----------

class TestLead:
    def test_defaults_empty(self):
        lead = agent.Lead()
        for f in agent.FIELDS:
            assert getattr(lead, f) == ""

    def test_fields_order(self):
        assert agent.FIELDS == [
            "company_name",
            "website",
            "email",
            "phone",
            "location",
            "description",
            "source_url",
        ]


# ---------- CSV ----------

class TestSaveCsv:
    def test_writes_all_fields(self, tmp_output_dir):
        leads = [
            agent.Lead(
                company_name="Northern Lights Animation",
                website="https://northernlightsanim.com",
                email="hello@northernlightsanim.com",
                phone="+1 604-555-0198",
                location="Vancouver, BC",
                description="Boutique animation studio",
                source_url="https://clutch.co/profile/nla",
            ),
        ]
        path = agent.save_csv(leads, "clutch.co", "animation studio")
        assert path.exists()
        assert path.parent == tmp_output_dir
        assert path.name.startswith("agent_leads_clutch-co_animation-studio_")

        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["email"] == "hello@northernlightsanim.com"
        assert rows[0]["website"] == "https://northernlightsanim.com"

    def test_slugifies_site_with_slashes(self, tmp_output_dir):
        path = agent.save_csv([], "https://sub.example.co/uk", "Motion Design")
        assert "sub-example-co-uk" in path.name or "example-co-uk" in path.name
        assert "motion-design" in path.name

    def test_empty_leads_still_has_header(self, tmp_output_dir):
        path = agent.save_csv([], "x.com", "k")
        with path.open() as fh:
            header = fh.readline().strip().split(",")
        assert header == agent.FIELDS


# ---------- polite_sleep ----------

class TestPoliteSleep:
    def test_within_bounds(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(time, "sleep", lambda s: captured.update(seconds=s))
        agent.polite_sleep(1.5, 3.5)
        assert 1.5 <= captured["seconds"] <= 3.5


# ---------- main() behavior ----------

class TestMain:
    def test_returns_1_when_no_leads(self, monkeypatch, tmp_output_dir):
        monkeypatch.setattr(agent, "run", lambda **kw: [])
        rc = agent.main(["--keyword", "x", "--site", "y.com"])
        assert rc == 1

    def test_returns_0_and_writes_csv(self, monkeypatch, tmp_output_dir):
        monkeypatch.setattr(
            agent,
            "run",
            lambda **kw: [agent.Lead(company_name="Solo")],
        )
        rc = agent.main(["--keyword", "x", "--site", "y.com"])
        assert rc == 0
        csvs = list(tmp_output_dir.glob("agent_leads_*.csv"))
        assert len(csvs) == 1
