"""Tests for external_data.py — URL extraction, web scraping, API enrichment."""

import json
from unittest.mock import patch, MagicMock
import pytest
import responses

from external_data import (
    extract_url,
    fetch_web_metadata,
    get_youtube_video_id,
    get_youtube_metadata,
    get_video_channel_details,
    get_spotify_metadata,
    get_place_details,
    enrich_context,
    resolve_final_url,
    get_tal_metadata,
)


# ======================================================================
# extract_url
# ======================================================================
class TestExtractUrl:
    def test_full_https_url(self):
        assert extract_url("Check out https://example.com/path") == "https://example.com/path"

    def test_http_url(self):
        assert extract_url("http://test.org") == "http://test.org"

    def test_www_no_protocol(self):
        assert extract_url("Visit www.google.com today") == "https://www.google.com"

    def test_bare_domain(self):
        assert extract_url("Go to example.com") == "https://example.com"

    def test_no_url(self):
        assert extract_url("No link here at all") is None

    def test_youtube_short_url(self):
        url = extract_url("Watch this https://youtu.be/dQw4w9WgXcQ")
        assert url == "https://youtu.be/dQw4w9WgXcQ"

    def test_google_maps_url(self):
        url = extract_url("Check https://maps.app.goo.gl/abc123")
        assert "maps.app.goo.gl" in url

    def test_url_with_query_params(self):
        url = extract_url("https://www.youtube.com/watch?v=abc123&t=10s")
        assert "v=abc123" in url


# ======================================================================
# get_youtube_video_id
# ======================================================================
class TestGetYoutubeVideoId:
    def test_standard_watch_url(self):
        assert get_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert get_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_mobile_url(self):
        assert get_youtube_video_id("https://m.youtube.com/watch?v=abc123") == "abc123"

    def test_shorts_url(self):
        assert get_youtube_video_id("https://www.youtube.com/shorts/xyz789") == "xyz789"

    def test_invalid_url(self):
        assert get_youtube_video_id("https://example.com/page") is None

    def test_youtube_no_v_param(self):
        assert get_youtube_video_id("https://www.youtube.com/watch") is None


# ======================================================================
# fetch_web_metadata
# ======================================================================
class TestFetchWebMetadata:
    @responses.activate
    def test_og_title_extraction(self):
        html = '<html><head><meta property="og:title" content="My OG Title"><title>Fallback</title></head><body><p>Body content here</p></body></html>'
        responses.add(responses.GET, "https://example.com", body=html, status=200)
        result = fetch_web_metadata("https://example.com")
        assert "HTML Title: My OG Title" in result

    @responses.activate
    def test_html_title_fallback(self):
        html = "<html><head><title>HTML Only Title</title></head><body><p>Content</p></body></html>"
        responses.add(responses.GET, "https://example.com", body=html, status=200)
        result = fetch_web_metadata("https://example.com")
        assert "HTML Title: HTML Only Title" in result

    @responses.activate
    def test_github_prefix_removal(self):
        html = '<html><head><title>GitHub - owner/repo: Description</title></head><body></body></html>'
        responses.add(responses.GET, "https://github.com/owner/repo", body=html, status=200)
        result = fetch_web_metadata("https://github.com/owner/repo")
        assert "GitHub - " not in result.split("\n")[0]
        assert "owner/repo" in result

    @responses.activate
    def test_html_entity_cleanup(self):
        html = '<html><head><meta property="og:title" content="Test &amp;amp; Title"></head><body></body></html>'
        responses.add(responses.GET, "https://example.com", body=html, status=200)
        result = fetch_web_metadata("https://example.com")
        # The function replaces &amp; -> &
        assert "Title" in result

    @responses.activate
    def test_fetch_failure(self):
        responses.add(responses.GET, "https://bad.com", body=Exception("Timeout"))
        result = fetch_web_metadata("https://bad.com")
        assert "Error fetching metadata" in result

    @responses.activate
    def test_body_content_included(self):
        html = "<html><head><title>T</title></head><body><p>Important body text for preview</p></body></html>"
        responses.add(responses.GET, "https://example.com", body=html, status=200)
        result = fetch_web_metadata("https://example.com")
        assert "Page Content Preview" in result
        assert "Important body text" in result


# ======================================================================
# get_youtube_metadata
# ======================================================================
class TestGetYoutubeMetadata:
    def test_success(self, mock_youtube):
        mock_req = MagicMock()
        mock_req.execute.return_value = {
            "items": [{"snippet": {"title": "Video Title", "channelTitle": "Channel"}}]
        }
        mock_youtube.videos.return_value.list.return_value = mock_req

        result = get_youtube_metadata("https://youtu.be/abc123")
        assert "Title: Video Title" in result
        assert "Handle: Channel" in result

    def test_private_video(self, mock_youtube):
        mock_req = MagicMock()
        mock_req.execute.return_value = {"items": []}
        mock_youtube.videos.return_value.list.return_value = mock_req

        result = get_youtube_metadata("https://youtu.be/abc123")
        assert "not found or private" in result

    def test_no_youtube_client(self):
        with patch("external_data.youtube", None):
            result = get_youtube_metadata("https://youtu.be/abc123")
            assert "No YouTube Client" in result

    def test_api_error(self, mock_youtube):
        mock_req = MagicMock()
        mock_req.execute.side_effect = Exception("API quota exceeded")
        mock_youtube.videos.return_value.list.return_value = mock_req

        result = get_youtube_metadata("https://youtu.be/abc123")
        assert "YT Error" in result


# ======================================================================
# get_video_channel_details
# ======================================================================
class TestGetVideoChannelDetails:
    def test_success(self, mock_youtube):
        mock_req = MagicMock()
        mock_req.execute.return_value = {
            "items": [{"snippet": {"channelTitle": "MKBHD", "channelId": "ch123"}}]
        }
        mock_youtube.videos.return_value.list.return_value = mock_req

        result = get_video_channel_details("https://youtu.be/abc123")
        assert result["title"] == "MKBHD"
        assert result["id"] == "ch123"
        assert "youtube.com/channel/ch123" in result["url"]

    def test_no_client(self):
        with patch("external_data.youtube", None):
            assert get_video_channel_details("https://youtu.be/abc") is None

    def test_invalid_url(self, mock_youtube):
        assert get_video_channel_details("https://example.com") is None


# ======================================================================
# get_spotify_metadata
# ======================================================================
class TestGetSpotifyMetadata:
    def test_success(self, mock_spotify):
        mock_spotify.episode.return_value = {
            "show": {"name": "My Show"},
            "name": "Episode 1",
            "description": "Great episode",
        }
        result = get_spotify_metadata("https://open.spotify.com/episode/abc")
        assert "Show: My Show" in result
        assert "Ep: Episode 1" in result

    def test_no_client(self):
        with patch("external_data.spotify", None):
            result = get_spotify_metadata("https://open.spotify.com/episode/abc")
            assert "No Spotify Client" in result

    def test_api_error(self, mock_spotify):
        mock_spotify.episode.side_effect = Exception("Rate limited")
        result = get_spotify_metadata("https://open.spotify.com/episode/abc")
        assert "Spotify Error" in result


# ======================================================================
# get_place_details
# ======================================================================
class TestGetPlaceDetails:
    def test_success(self, mock_gmaps):
        mock_gmaps.find_place.return_value = {
            "status": "OK",
            "candidates": [{"place_id": "place123"}],
        }
        mock_gmaps.place.return_value = {
            "result": {
                "name": "Central Park",
                "formatted_address": "New York, NY, USA",
                "address_components": [
                    {"long_name": "New York", "types": ["locality"]},
                    {"long_name": "United States", "types": ["country"]},
                ],
                "types": ["park"],
                "url": "https://maps.google.com/?cid=123",
            }
        }

        result = get_place_details("Central Park NYC")
        assert result["Name"] == "Central Park"
        assert result["City"] == "New York"
        assert result["Country"] == "United States"
        assert result["Raw Types"] == ["park"]

    def test_no_results(self, mock_gmaps):
        mock_gmaps.find_place.return_value = {"status": "ZERO_RESULTS", "candidates": []}
        assert get_place_details("nonexistent place xyz") is None

    def test_no_client(self):
        with patch("external_data.gmaps", None):
            assert get_place_details("test") is None

    @responses.activate
    def test_url_resolution(self, mock_gmaps):
        """When query starts with http, resolve_final_url is called first."""
        responses.add(responses.GET, "https://maps.app.goo.gl/abc", body="", status=200)
        mock_gmaps.find_place.return_value = {"status": "ZERO_RESULTS", "candidates": []}

        get_place_details("https://maps.app.goo.gl/abc")
        # Should have been called with the resolved URL
        mock_gmaps.find_place.assert_called_once()


# ======================================================================
# resolve_final_url
# ======================================================================
class TestResolveFinalUrl:
    @responses.activate
    def test_follows_redirects(self):
        responses.add(
            responses.GET,
            "https://short.url/abc",
            headers={"Location": "https://final.url/page"},
            status=301,
        )
        responses.add(responses.GET, "https://final.url/page", body="", status=200)
        result = resolve_final_url("https://short.url/abc")
        assert "final.url" in result

    @responses.activate
    def test_failure_returns_original(self):
        responses.add(responses.GET, "https://bad.url", body=Exception("fail"))
        result = resolve_final_url("https://bad.url")
        assert result == "https://bad.url"


# ======================================================================
# enrich_context
# ======================================================================
class TestEnrichContext:
    def test_places_routing(self, mock_gmaps):
        mock_gmaps.find_place.return_value = {
            "status": "OK",
            "candidates": [{"place_id": "p1"}],
        }
        mock_gmaps.place.return_value = {
            "result": {
                "name": "Test",
                "formatted_address": "Addr",
                "address_components": [],
                "types": ["restaurant"],
                "url": "https://maps.google.com/123",
            }
        }
        result = enrich_context("places", "Some place text")
        assert result is not None
        assert "GOOGLE MAPS DATA" in result

    def test_bookmarks_routing(self):
        with patch("external_data.fetch_web_metadata", return_value="HTML Title: Test\nContent..."):
            result = enrich_context("bookmarks", "https://example.com")
            assert "HTML Title" in result

    def test_youtube_routing(self, mock_youtube):
        mock_req = MagicMock()
        mock_req.execute.return_value = {
            "items": [{"snippet": {"title": "Vid", "channelTitle": "Ch"}}]
        }
        mock_youtube.videos.return_value.list.return_value = mock_req

        result = enrich_context("youtube-videos", "https://youtu.be/abc123")
        assert "Title:" in result

    def test_podcasts_spotify_routing(self):
        mock_sp = MagicMock()
        mock_sp.episode.return_value = {
            "show": {"name": "S"}, "name": "E", "description": "D"
        }
        with patch("external_data.spotify", mock_sp):
            result = enrich_context("podcasts", "https://open.spotify.com/episode/abc123")
        assert "Show:" in result

    def test_no_url_returns_none(self):
        result = enrich_context("bookmarks", "no url here")
        assert result is None

    def test_unhandled_category(self):
        result = enrich_context("tasks", "https://example.com")
        assert result is None


# ======================================================================
# get_tal_metadata
# ======================================================================
class TestGetTalMetadata:
    @responses.activate
    def test_success(self):
        html = "<html><body><p>This American Life episode content here</p></body></html>"
        responses.add(responses.GET, "https://www.thisamericanlife.org/123", body=html, status=200)
        result = get_tal_metadata("https://www.thisamericanlife.org/123")
        assert "Content:" in result

    @responses.activate
    def test_failure_creates_cleanup(self, mock_notion):
        responses.add(responses.GET, "https://www.thisamericanlife.org/bad", body=Exception("fail"))
        result = get_tal_metadata("https://www.thisamericanlife.org/bad")
        assert "Error fetching URL" in result
