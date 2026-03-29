"""Tests for external_data.py — Google Maps URL sanitization."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(
    "sys.modules",
    {
        "config": MagicMock(DATABASES={"databases": {}}),
        "gcp_secrets": MagicMock(),
        "clients": MagicMock(spotify=None, youtube=None, gmaps=None),
        "notion_utils": MagicMock(),
    },
):
    from external_data import sanitize_google_maps_url


class TestGoogleMapsUrlSanitization:
    def test_strips_tracking_params(self):
        url = (
            "https://www.google.com/maps/place/Central+Park/"
            "?g_st=ib&g_ep=foo&lucs=bar&skid=baz&q=Central+Park"
        )
        result = sanitize_google_maps_url(url)
        assert "g_st" not in result
        assert "g_ep" not in result
        assert "lucs" not in result
        assert "q=Central+Park" in result

    def test_keeps_essential_params(self):
        url = "https://www.google.com/maps/place/Test/?q=coffee&place_id=abc123&ftid=xyz"
        result = sanitize_google_maps_url(url)
        assert "q=coffee" in result
        assert "place_id=abc123" in result
        assert "ftid=xyz" in result

    def test_non_google_url_unchanged(self):
        url = "https://www.example.com/maps?g_st=ib&foo=bar"
        result = sanitize_google_maps_url(url)
        assert result == url

    def test_clean_url_unchanged(self):
        url = "https://www.google.com/maps/place/Central+Park/"
        result = sanitize_google_maps_url(url)
        assert "google.com/maps/place/Central+Park/" in result

    def test_very_long_tracking_url(self):
        base = "https://www.google.com/maps/place/Some+Place/"
        tracking = "&".join(f"track{i}=value{i}" for i in range(50))
        url = f"{base}?q=test&{tracking}"
        result = sanitize_google_maps_url(url)
        assert len(result) < len(url)
        assert "q=test" in result

    def test_maps_app_goo_gl_domain(self):
        url = "https://maps.app.goo.gl/abc123?g_st=something"
        result = sanitize_google_maps_url(url)
        assert "g_st" not in result

    def test_preserves_path(self):
        url = "https://www.google.com/maps/place/Central+Park/@40.7,-73.9,15z/?q=park&junk=yes"
        result = sanitize_google_maps_url(url)
        assert "/maps/place/Central+Park/@40.7,-73.9,15z/" in result
        assert "q=park" in result
        assert "junk" not in result
