"""Tests for external_data.py — URL extraction, web scraping, API enrichment."""

from unittest.mock import patch, MagicMock
import responses

from core.external_data import (
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
    sanitize_youtube_url,
    get_tmdb_metadata,
    map_genres,
)


# ======================================================================
# sanitize_youtube_url
# ======================================================================
class TestSanitizeYoutubeUrl:
    def test_strips_timestamp(self):
        url = "https://www.youtube.com/watch?v=X&t=123s"
        assert sanitize_youtube_url(url) == "https://www.youtube.com/watch?v=X"

    def test_strips_si_and_t_from_short_link(self):
        url = "https://youtu.be/X?si=abc123&t=1m2s"
        assert sanitize_youtube_url(url) == "https://youtu.be/X"

    def test_strips_feature(self):
        url = "https://www.youtube.com/watch?v=X&feature=share"
        assert sanitize_youtube_url(url) == "https://www.youtube.com/watch?v=X"

    def test_clean_url_unchanged(self):
        url = "https://www.youtube.com/watch?v=X"
        assert sanitize_youtube_url(url) == url

    def test_no_query_unchanged(self):
        url = "https://youtu.be/X"
        assert sanitize_youtube_url(url) == url


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
        html = (
            "<html><head><title>GitHub - owner/repo: Description</title></head><body></body></html>"
        )
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
        with patch("core.external_data.get_youtube", return_value=None):
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
        with patch("core.external_data.get_youtube", return_value=None):
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
        with patch("core.external_data.get_spotify", return_value=None):
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
        with patch("core.external_data.get_gmaps", return_value=None):
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
        with patch(
            "core.external_data.fetch_web_metadata", return_value="HTML Title: Test\nContent..."
        ):
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
        mock_sp.episode.return_value = {"show": {"name": "S"}, "name": "E", "description": "D"}
        with patch("core.external_data.get_spotify", return_value=mock_sp):
            result = enrich_context("podcasts", "https://open.spotify.com/episode/abc123")
        assert "Show:" in result

    def test_no_url_returns_none(self):
        result = enrich_context("bookmarks", "no url here")
        assert result is None

    def test_unhandled_category(self):
        result = enrich_context("tasks", "https://example.com")
        assert result is None


# ======================================================================
# get_tmdb_metadata
# ======================================================================
MOVIE_SEARCH = "https://api.themoviedb.org/3/search/movie"
MOVIE_DETAIL = "https://api.themoviedb.org/3/movie/603"
TV_SEARCH = "https://api.themoviedb.org/3/search/tv"
TV_DETAIL = "https://api.themoviedb.org/3/tv/1396"


class TestGetTmdbMetadata:
    @responses.activate
    def test_movie_happy_path(self):
        responses.add(
            responses.GET,
            MOVIE_SEARCH,
            json={
                "results": [
                    {
                        "id": 603,
                        "title": "The Matrix",
                        "release_date": "1999-03-30",
                        "vote_count": 26000,
                    }
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            MOVIE_DETAIL,
            json={
                "genres": [{"name": "Science Fiction"}, {"name": "Action"}],
                "credits": {
                    "crew": [
                        {"job": "Writer", "name": "Nope"},
                        {"job": "Director", "name": "Lana Wachowski"},
                    ],
                    "cast": [{"name": f"Actor {i}"} for i in range(10)],
                },
            },
            status=200,
        )
        with patch.dict("os.environ", {"TMDB_API_KEY": "fake-key"}):
            meta = get_tmdb_metadata("The Matrix", "movie")

        assert meta["genres"] == ["Science Fiction", "Action"]
        assert meta["director"] == "Lana Wachowski"
        assert meta["cast"] == [f"Actor {i}" for i in range(5)]  # top ~5
        assert meta["matched_title"] == "The Matrix"
        assert meta["year"] == "1999"

    @responses.activate
    def test_tv_uses_created_by_for_director(self):
        responses.add(
            responses.GET,
            TV_SEARCH,
            json={
                "results": [
                    {
                        "id": 1396,
                        "name": "Breaking Bad",
                        "first_air_date": "2008-01-20",
                        "vote_count": 15000,
                    }
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            TV_DETAIL,
            json={
                "genres": [{"name": "Drama"}],
                "created_by": [{"name": "Vince Gilligan"}],
                "credits": {"crew": [], "cast": [{"name": "Bryan Cranston"}]},
            },
            status=200,
        )
        with patch.dict("os.environ", {"TMDB_API_KEY": "fake-key"}):
            meta = get_tmdb_metadata("Breaking Bad", "tv")

        assert meta["director"] == "Vince Gilligan"
        assert meta["cast"] == ["Bryan Cranston"]

    def test_no_key_returns_none(self):
        with patch.dict("os.environ", {"TMDB_API_KEY": ""}):
            assert get_tmdb_metadata("The Matrix", "movie") is None

    @responses.activate
    def test_no_search_result_returns_none(self):
        responses.add(responses.GET, MOVIE_SEARCH, json={"results": []}, status=200)
        with patch.dict("os.environ", {"TMDB_API_KEY": "fake-key"}):
            assert get_tmdb_metadata("Nonexistent Film", "movie") is None

    @responses.activate
    def test_http_error_returns_none(self):
        responses.add(responses.GET, MOVIE_SEARCH, status=500)
        with patch.dict("os.environ", {"TMDB_API_KEY": "fake-key"}):
            assert get_tmdb_metadata("The Matrix", "movie") is None

    @responses.activate
    def test_low_vote_junk_match_returns_none(self):
        """A top hit with almost no votes is fan-content, not the film Alex means —
        return None so the _tmdb_failed cleanup path runs instead of trusting it."""
        responses.add(
            responses.GET,
            MOVIE_SEARCH,
            json={"results": [{"id": 9, "title": "Into the Backrooms", "vote_count": 1}]},
            status=200,
        )
        with patch.dict("os.environ", {"TMDB_API_KEY": "fake-key"}):
            assert get_tmdb_metadata("Backrooms II", "movie") is None

    @responses.activate
    def test_the_prefix_retry_finds_real_film(self):
        """'The Backrooms' search returns only junk; the stripped 'Backrooms' retry
        finds the real film and its title/year are surfaced as the match."""
        responses.add(
            responses.GET,
            MOVIE_SEARCH,
            json={"results": [{"id": 9, "title": "Into the Backrooms", "vote_count": 1}]},
            status=200,
        )
        responses.add(
            responses.GET,
            MOVIE_SEARCH,
            json={
                "results": [
                    {
                        "id": 42,
                        "title": "Backrooms",
                        "release_date": "2026-07-24",
                        "vote_count": 3046,
                    }
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.themoviedb.org/3/movie/42",
            json={
                "genres": [{"name": "Horror"}],
                "credits": {
                    "crew": [{"job": "Director", "name": "Kane Parsons"}],
                    "cast": [{"name": "Chiwetel Ejiofor"}],
                },
            },
            status=200,
        )
        with patch.dict("os.environ", {"TMDB_API_KEY": "fake-key"}):
            meta = get_tmdb_metadata("The Backrooms", "movie")
        assert meta["matched_title"] == "Backrooms"
        assert meta["year"] == "2026"
        assert meta["director"] == "Kane Parsons"


# ======================================================================
# map_genres
# ======================================================================
class TestMapGenres:
    def test_science_fiction_alias_to_existing_sci_fi(self):
        assert map_genres(["Science Fiction"], ["Sci-Fi", "Action"]) == ["Sci-Fi"]

    def test_unknown_genre_passes_through(self):
        assert map_genres(["Mumblecore"], ["Sci-Fi", "Action"]) == ["Mumblecore"]

    def test_case_insensitive_uses_existing_casing(self):
        assert map_genres(["documentary"], ["Documentary"]) == ["Documentary"]

    def test_alias_target_absent_keeps_tmdb_name(self):
        # No "Sci-Fi" option exists -> the TMDB name is kept (multi_select auto-creates)
        assert map_genres(["Science Fiction"], ["Drama"]) == ["Science Fiction"]

    def test_dedupes(self):
        assert map_genres(["Action", "Action"], ["Action"]) == ["Action"]


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
