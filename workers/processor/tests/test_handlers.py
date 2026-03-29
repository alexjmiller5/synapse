"""Tests for handlers.py — category-specific logic for all Notion DB categories."""

from unittest.mock import patch, MagicMock, call
import pytest

from handlers import (
    handle_places_logic,
    handle_groceries_fun_logic,
    handle_youtube_logic,
    handle_movies_tv_logic,
    handle_bookmarks_logic,
    handle_people_logic,
    handle_bucket_list_logic,
    handle_default_logic,
)
from helpers import make_notion_page


# ======================================================================
# handle_places_logic
# ======================================================================
class TestHandlePlaces:
    def test_new_place_creation(self, mock_notion):
        data = {
            "Name": "Central Park",
            "Status": "Haven't Been",
            "Address": "NYC",
            "City": "New York",
            "Country": "United States",
            "Google Maps URL": "https://maps.google.com/123",
        }
        mock_notion.request.return_value = {"results": []}  # No duplicate

        url = handle_places_logic("places", data, trips_id_map={})
        mock_notion.pages.create.assert_called_once()
        assert url is not None

    def test_duplicate_update(self, mock_notion):
        data = {
            "Name": "Central Park",
            "Status": "Been",
            "Google Maps URL": "https://maps.google.com/123",
        }
        existing = make_notion_page("existing-place-id", "Name", "Central Park")
        # The handler calls notion.request for the dedup query
        mock_notion.request.return_value = {"results": [existing]}

        url = handle_places_logic("places", data, trips_id_map={})
        mock_notion.pages.update.assert_called()
        assert "existingplaceid" in (url or "").replace("-", "")
        # Should NOT create a new page
        mock_notion.pages.create.assert_not_called()

    def test_trip_linking_new_place(self, mock_notion):
        data = {
            "Name": "Restaurant",
            "Status": "Haven't Been",
            "Google Maps URL": "https://maps.google.com/456",
            "Linked Trip": "NYC Trip",
        }
        mock_notion.request.return_value = {"results": []}
        trips_map = {"NYC Trip": "trip-id-123"}

        handle_places_logic("places", data, trips_id_map=trips_map)
        # Should create place then link trip via update
        assert mock_notion.pages.create.called
        update_calls = mock_notion.pages.update.call_args_list
        assert any(
            "Linked Trip" in str(c) for c in update_calls
        )

    def test_trip_linking_existing_place(self, mock_notion):
        data = {
            "Name": "Restaurant",
            "Status": "Been",
            "Google Maps URL": "https://maps.google.com/789",
            "Linked Trip": "LA Trip",
        }
        existing = make_notion_page("existing-id", "Name", "Restaurant")
        mock_notion.request.return_value = {"results": [existing]}
        trips_map = {"LA Trip": "la-trip-id"}

        handle_places_logic("places", data, trips_id_map=trips_map)
        update_call = mock_notion.pages.update.call_args
        props = update_call.kwargs.get("properties", {})
        assert "Linked Trip" in props

    def test_no_google_maps_url(self, mock_notion):
        data = {"Name": "Some Place", "Status": "Haven't Been"}
        mock_notion.request.return_value = {"results": []}

        url = handle_places_logic("places", data, trips_id_map={})
        mock_notion.pages.create.assert_called_once()


# ======================================================================
# handle_groceries_fun_logic
# ======================================================================
class TestHandleGroceriesFun:
    def test_groceries_existing_item_update(self, mock_notion):
        data = {"Name": "Eggs", "Status": "On List"}
        inventory = {"Eggs": "eggs-page-id"}

        url = handle_groceries_fun_logic("groceries", data, inventory)
        mock_notion.pages.update.assert_called_once()
        # Should NOT create new
        mock_notion.pages.create.assert_not_called()

    def test_groceries_new_item(self, mock_notion):
        data = {"Name": "Quinoa", "Status": "On List", "Category": "Grains"}

        url = handle_groceries_fun_logic("groceries", data, inventory_map={})
        mock_notion.pages.create.assert_called_once()

    def test_fun_activities_new_with_location(self, mock_notion):
        mock_notion.request.return_value = {"results": []}  # No duplicate
        data = {"Title": "Walk Seaport", "Status": "To Do", "Location": "Boston"}

        url = handle_groceries_fun_logic("fun-activities", data, inventory_map=None)
        mock_notion.pages.create.assert_called_once()

    def test_fun_activities_no_location_creates_cleanup(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {"Title": "Go Kayaking", "Status": "To Do"}

        url = handle_groceries_fun_logic("fun-activities", data, inventory_map=None)
        # Should create page + cleanup task = 2 create calls
        assert mock_notion.pages.create.call_count == 2

    def test_fun_activities_existing_update(self, mock_notion):
        existing = make_notion_page("fun-id", "Title", "Walk Seaport")
        # fetch_existing_page calls notion.request
        mock_notion.request.return_value = {"results": [existing]}
        data = {"Title": "Walk Seaport", "Status": "Done"}

        url = handle_groceries_fun_logic("fun-activities", data, inventory_map=None)
        mock_notion.pages.update.assert_called()


# ======================================================================
# handle_youtube_logic
# ======================================================================
class TestHandleYoutube:
    def test_new_video_with_channel(self, mock_notion):
        mock_notion.request.return_value = {"results": []}  # No duplicate video or channel

        with patch("handlers.get_video_channel_details") as mock_channel:
            mock_channel.return_value = {
                "title": "MKBHD", "id": "ch1", "url": "https://youtube.com/channel/ch1"
            }
            data = {"Title": "Review", "Video URL": "https://youtu.be/abc", "Status": "Watched"}
            url = handle_youtube_logic("youtube-videos", data)

            assert mock_notion.pages.create.called
            # Should have created channel + video + cleanup = 3 creates
            assert mock_notion.pages.create.call_count >= 2

    def test_duplicate_video_update(self, mock_notion):
        existing = make_notion_page("vid-id", "Title", "Old Video")
        # The dedup query finds the existing video
        mock_notion.request.return_value = {"results": [existing]}

        data = {"Title": "Old Video", "Video URL": "https://youtu.be/abc", "Status": "Watched"}
        url = handle_youtube_logic("youtube-videos", data)
        mock_notion.pages.update.assert_called()

    def test_no_channel_api_uses_handle(self, mock_notion):
        mock_notion.request.return_value = {"results": []}

        with patch("handlers.get_video_channel_details", return_value=None):
            data = {
                "Title": "Video",
                "Video URL": "https://youtu.be/abc",
                "Status": "Watched",
                "channel_handle": "@TestChannel",
            }
            handle_youtube_logic("youtube-videos", data)
            # Should still create the video
            mock_notion.pages.create.assert_called()


# ======================================================================
# handle_movies_tv_logic
# ======================================================================
class TestHandleMoviesTv:
    def test_new_movie(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {"Title": "Inception", "Status": "Not Started", "Genres": ["Sci-Fi"]}

        url = handle_movies_tv_logic("movies", data)
        mock_notion.pages.create.assert_called_once()

    def test_existing_movie_significant_status(self, mock_notion):
        existing = make_notion_page("movie-id", "Title", "Inception")
        # fetch_existing_page calls notion.request
        mock_notion.request.return_value = {"results": [existing]}
        data = {"Title": "Inception", "Status": "Finished"}

        url = handle_movies_tv_logic("movies", data)
        mock_notion.pages.update.assert_called()
        mock_notion.pages.create.assert_not_called()

    def test_existing_movie_insignificant_status(self, mock_notion):
        existing = make_notion_page("movie-id", "Title", "Inception")
        mock_notion.request.return_value = {"results": [existing]}
        data = {"Title": "Inception", "Status": "Not Started"}

        url = handle_movies_tv_logic("movies", data)
        # Should NOT update or create — just return URL
        mock_notion.pages.update.assert_not_called()
        mock_notion.pages.create.assert_not_called()
        assert "movieid" in (url or "").replace("-", "")

    def test_tv_show_same_logic(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {"Title": "Severance", "Status": "Finished", "Genres": ["Drama"]}

        url = handle_movies_tv_logic("tv-shows", data)
        mock_notion.pages.create.assert_called_once()


# ======================================================================
# handle_bookmarks_logic
# ======================================================================
class TestHandleBookmarks:
    def test_new_bookmark(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {"Description": "A site", "Title": "Example", "URL": "https://example.com", "Tags": []}

        url = handle_bookmarks_logic("bookmarks", data)
        mock_notion.pages.create.assert_called_once()

    def test_duplicate_bookmark(self, mock_notion):
        existing = make_notion_page("bm-id", "Description", "Old bookmark")
        mock_notion.request.return_value = {"results": [existing]}
        data = {"Description": "A site", "URL": "https://example.com"}

        url = handle_bookmarks_logic("bookmarks", data)
        mock_notion.pages.create.assert_not_called()
        assert "bmid" in (url or "").replace("-", "")

    def test_github_tagging(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {"Description": "Repo", "URL": "https://github.com/owner/repo", "Tags": []}

        handle_bookmarks_logic("bookmarks", data)
        create_call = mock_notion.pages.create.call_args
        props = create_call.kwargs["properties"]
        # Tags should include Github (added by handler)
        # The tags come through build_notion_properties so check the raw data was modified
        assert "Github" in data["Tags"]


# ======================================================================
# handle_people_logic
# ======================================================================
class TestHandlePeople:
    def test_creates_person(self, mock_notion):
        data = {"Name": "Rishi Patel", "Company": "TDP Senior Associate"}
        url = handle_people_logic("people", data)
        mock_notion.pages.create.assert_called_once()
        assert url is not None


# ======================================================================
# handle_bucket_list_logic
# ======================================================================
class TestHandleBucketList:
    def test_creates_item(self, mock_notion):
        data = {"Item": "Skydive in Dubai", "Tags": ["Adventure"]}
        url = handle_bucket_list_logic("bucket-list", data)
        mock_notion.pages.create.assert_called_once()


# ======================================================================
# handle_default_logic
# ======================================================================
class TestHandleDefault:
    def test_creates_page(self, mock_notion):
        data = {"Description": "Random idea", "Tags": ["Tech"]}
        url = handle_default_logic("ideas", data)
        mock_notion.pages.create.assert_called_once()

    def test_quotes_no_context_creates_cleanup(self, mock_notion):
        data = {"Quote": "\u201cI will be back\u201d"}
        handle_default_logic("quotes", data)
        # Should create quote page + cleanup task = 2 create calls
        assert mock_notion.pages.create.call_count == 2

    def test_quotes_with_context_no_cleanup(self, mock_notion):
        data = {"Quote": "\u201cI will be back\u201d", "Context": "Arnold in Terminator"}
        handle_default_logic("quotes", data)
        # Only the quote page, no cleanup
        assert mock_notion.pages.create.call_count == 1
