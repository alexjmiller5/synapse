"""Tests for handlers.py — category-specific logic for all Notion DB categories."""

import re
from unittest.mock import patch

import pytest

from core.handlers import (
    handle_places_logic,
    handle_groceries_fun_logic,
    handle_youtube_logic,
    handle_movies_tv_logic,
    handle_bookmarks_logic,
    handle_people_logic,
    handle_bucket_list_logic,
    handle_default_logic,
)
from core.notion_utils import prop_id
from helpers import make_notion_page, props_of, sent_props


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
        linked_trip_id = prop_id("places", "Linked Trip")
        assert any(linked_trip_id in str(c) for c in update_calls)

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
        props = props_of(mock_notion.pages.update.call_args, "places")
        assert "Linked Trip" in props

    def test_no_google_maps_url(self, mock_notion):
        data = {"Name": "Some Place", "Status": "Haven't Been"}
        mock_notion.request.return_value = {"results": []}

        handle_places_logic("places", data, trips_id_map={})
        mock_notion.pages.create.assert_called_once()


# ======================================================================
# handle_groceries_fun_logic
# ======================================================================
class TestHandleGroceriesFun:
    def test_groceries_existing_item_update(self, mock_notion):
        data = {"Name": "Eggs", "Status": "On List"}
        inventory = {"Eggs": "eggs-page-id"}

        handle_groceries_fun_logic("groceries", data, inventory)
        mock_notion.pages.update.assert_called_once()
        # Should NOT create new
        mock_notion.pages.create.assert_not_called()

    def test_groceries_new_item(self, mock_notion):
        data = {"Name": "Quinoa", "Status": "On List", "Category": "Grains"}

        handle_groceries_fun_logic("groceries", data, inventory_map={})
        mock_notion.pages.create.assert_called_once()

    def test_fun_activities_new_with_location(self, mock_notion):
        mock_notion.request.return_value = {"results": []}  # No duplicate
        data = {"Title": "Walk Seaport", "Status": "To Do", "Location": "Boston"}

        handle_groceries_fun_logic("fun-activities", data, inventory_map=None)
        mock_notion.pages.create.assert_called_once()

    def test_fun_activities_no_location_creates_cleanup(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {"Title": "Go Kayaking", "Status": "To Do"}

        handle_groceries_fun_logic("fun-activities", data, inventory_map=None)
        # Should create page + cleanup task = 2 create calls
        assert mock_notion.pages.create.call_count == 2

    def test_fun_activities_existing_update(self, mock_notion):
        existing = make_notion_page("fun-id", "Title", "Walk Seaport")
        # fetch_existing_page calls notion.request
        mock_notion.request.return_value = {"results": [existing]}
        data = {"Title": "Walk Seaport", "Status": "Done"}

        handle_groceries_fun_logic("fun-activities", data, inventory_map=None)
        mock_notion.pages.update.assert_called()


# ======================================================================
# handle_youtube_logic
# ======================================================================
class TestHandleYoutube:
    def test_new_video_with_channel(self, mock_notion):
        mock_notion.request.return_value = {"results": []}  # No duplicate video or channel

        with patch("core.handlers.get_video_channel_details") as mock_channel:
            mock_channel.return_value = {
                "title": "MKBHD",
                "id": "ch1",
                "url": "https://youtube.com/channel/ch1",
            }
            data = {"Title": "Review", "Video URL": "https://youtu.be/abc", "Status": "Watched"}
            handle_youtube_logic("youtube-videos", data)

            assert mock_notion.pages.create.called
            # Should have created channel + video + cleanup = 3 creates
            assert mock_notion.pages.create.call_count >= 2

    def test_no_video_id_raises(self, mock_notion):
        """A YouTube URL with no video id (homepage/channel page) must fail loudly —
        never create a junk 'Could not extract Video ID' page."""
        data = {"Title": "Could not extract Video ID", "Video URL": "https://youtube.com/"}
        with pytest.raises(ValueError, match="No YouTube video ID"):
            handle_youtube_logic("youtube-videos", data)
        mock_notion.pages.create.assert_not_called()

    def test_missing_video_url_raises(self, mock_notion):
        with pytest.raises(ValueError, match="No YouTube video ID"):
            handle_youtube_logic("youtube-videos", {"Title": "No URL at all"})
        mock_notion.pages.create.assert_not_called()

    def test_duplicate_video_update(self, mock_notion):
        existing = make_notion_page("vid-id", "Title", "Old Video")
        # The dedup query finds the existing video
        mock_notion.request.return_value = {"results": [existing]}

        data = {"Title": "Old Video", "Video URL": "https://youtu.be/abc", "Status": "Watched"}
        handle_youtube_logic("youtube-videos", data)
        mock_notion.pages.update.assert_called()

    def test_no_channel_api_uses_handle(self, mock_notion):
        mock_notion.request.return_value = {"results": []}

        with patch("core.handlers.get_video_channel_details", return_value=None):
            data = {
                "Title": "Video",
                "Video URL": "https://youtu.be/abc",
                "Status": "Watched",
                "channel_handle": "@TestChannel",
            }
            handle_youtube_logic("youtube-videos", data)
            # Should still create the video
            mock_notion.pages.create.assert_called()

    def test_video_url_sanitized_before_storage(self, mock_notion):
        """Timestamp/tracking params are stripped before dedupe + storage."""
        mock_notion.request.return_value = {"results": []}

        with patch("core.handlers.get_video_channel_details", return_value=None):
            data = {
                "Title": "Video",
                "Video URL": "https://youtu.be/abc?si=XyZ123&t=1m2s",
                "Status": "Watched",
            }
            handle_youtube_logic("youtube-videos", data)

        props = sent_props(mock_notion.pages.create, "youtube-videos")
        assert props["Video URL"]["url"] == "https://youtu.be/abc"
        # The dedupe query used the sanitized URL too
        dedupe_body = mock_notion.request.call_args.kwargs["body"]
        assert dedupe_body["filter"]["url"]["equals"] == "https://youtu.be/abc"


# ======================================================================
# handle_movies_tv_logic - movies/TV live in life-data, not Notion
# ======================================================================
ISO_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class TestHandleMoviesTv:
    def test_confident_match_pushes_one_row(self, mock_notion):
        data = {"Title": "Inception", "Status": "Not Started", "Tags": ["All-time Favorite"]}
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="27205") as resolve,
            patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []}) as push,
        ):
            ref = handle_movies_tv_logic("movies", data)

        resolve.assert_called_once_with("movie", "Inception")
        push.assert_called_once()
        table, rows = push.call_args.args
        assert table == "movies"
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == "27205"
        assert row["status"] == "Not Started"
        assert row["tags"] == ["All-time Favorite"]
        assert ISO_MS.match(row["updated_at"])
        # Created Item is the life-data row reference, not a Notion URL
        assert ref == "movies/27205"
        # Nothing goes to Notion for these categories any more
        mock_notion.pages.create.assert_not_called()
        mock_notion.pages.update.assert_not_called()

    def test_tags_omitted_when_not_extracted(self):
        """Push only the columns you have - the hub upsert touches only those, so a
        status update must not blank an existing row's tags."""
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="27205"),
            patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []}) as push,
        ):
            handle_movies_tv_logic("movies", {"Title": "Inception", "Status": "Finished"})
        assert set(push.call_args.args[1][0]) == {"id", "status", "updated_at"}

    def test_tv_shows_push_to_tv_shows_table(self):
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="1396") as resolve,
            patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []}) as push,
        ):
            ref = handle_movies_tv_logic(
                "tv-shows", {"Title": "Breaking Bad", "Status": "Finished"}
            )
        resolve.assert_called_once_with("tv", "Breaking Bad")
        assert push.call_args.args[0] == "tv_shows"
        assert ref == "tv_shows/1396"

    def test_no_tmdb_match_files_cleanup_task_and_pushes_nothing(self, mock_notion):
        with (
            patch("core.handlers.resolve_tmdb_id", return_value=None),
            patch("core.handlers.push_rows") as push,
        ):
            handle_movies_tv_logic("movies", {"Title": "Some Obscure Film", "Status": "Priority"})

        push.assert_not_called()
        mock_notion.pages.create.assert_called_once()
        props = sent_props(mock_notion.pages.create, "tasks")
        assert "Some Obscure Film" in props["Name"]["title"][0]["text"]["content"]
        assert "TMDB" in props["Name"]["title"][0]["text"]["content"]

    def test_rejected_row_files_cleanup_task_with_the_rule_message(self, mock_notion):
        rejected = {
            "id": "27205",
            "col": "status",
            "rule": "options",
            "message": "status is not one of the allowed options",
        }
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="27205"),
            patch("core.handlers.push_rows", return_value={"upserted": 0, "rejected": [rejected]}),
        ):
            handle_movies_tv_logic("movies", {"Title": "Inception", "Status": "Bogus"})

        mock_notion.pages.create.assert_called_once()
        name = sent_props(mock_notion.pages.create, "tasks")["Name"]["title"][0]["text"]["content"]
        assert rejected["message"] in name


# ======================================================================
# handle_bookmarks_logic
# ======================================================================
class TestHandleBookmarks:
    def test_new_bookmark(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {
            "Description": "A site",
            "Title": "Example",
            "URL": "https://example.com",
            "Tags": [],
        }

        handle_bookmarks_logic("bookmarks", data)
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
        create_call.kwargs["properties"]
        # Tags should include Github (added by handler)
        # The tags come through build_notion_properties so check the raw data was modified
        assert "Github" in data["Tags"]

    def test_description_trailing_period_stripped(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {
            "Description": "A tool for secure dependency management.",
            "Title": "Example",
            "URL": "https://example.com",
            "Tags": [],
        }

        handle_bookmarks_logic("bookmarks", data)
        props = sent_props(mock_notion.pages.create, "bookmarks")
        desc = props["Description"]["title"][0]["text"]["content"]
        assert desc == "A tool for secure dependency management"

    def test_description_without_period_untouched(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        data = {
            "Description": "A tool for secure dependency management",
            "Title": "Example",
            "URL": "https://example.com",
            "Tags": [],
        }

        handle_bookmarks_logic("bookmarks", data)
        props = sent_props(mock_notion.pages.create, "bookmarks")
        assert (
            props["Description"]["title"][0]["text"]["content"]
            == "A tool for secure dependency management"
        )


# ======================================================================
# handle_people_logic
# ======================================================================
class TestHandlePeople:
    def test_creates_person(self, mock_notion):
        data = {"Name": "Arun Mehta", "Company": "Vantage Senior Associate"}
        url = handle_people_logic("people", data)
        mock_notion.pages.create.assert_called_once()
        assert url is not None


# ======================================================================
# handle_bucket_list_logic
# ======================================================================
class TestHandleBucketList:
    def test_creates_item(self, mock_notion):
        data = {"Item": "Skydive in Dubai", "Tags": ["Adventure"]}
        handle_bucket_list_logic("bucket-list", data)
        mock_notion.pages.create.assert_called_once()


# ======================================================================
# handle_default_logic
# ======================================================================
class TestHandleDefault:
    def test_creates_page(self, mock_notion):
        data = {"Description": "Random idea", "Tags": ["Tech"]}
        handle_default_logic("ideas", data)
        mock_notion.pages.create.assert_called_once()
