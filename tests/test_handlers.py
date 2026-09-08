"""Tests for handlers.py — category-specific logic for all Notion DB categories."""

import re
from unittest.mock import MagicMock, patch

import pytest

from core.handlers import (
    Failed,
    _to_hub_datetime,
    handle_groceries_fun_logic,
    handle_youtube_logic,
    handle_movies_tv_logic,
    handle_bookmarks_logic,
    handle_people_logic,
    handle_bucket_list_logic,
    handle_default_logic,
)
from helpers import make_notion_page, sent_props


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
# _to_hub_datetime - normalizes YouTube's ISO-8601 timestamps to the hub's
# required ISO-8601-UTC-with-milliseconds shape
# ======================================================================
class TestToHubDatetime:
    def test_z_no_fraction(self):
        assert _to_hub_datetime("2009-10-25T06:57:33Z") == "2009-10-25T06:57:33.000Z"

    def test_z_with_fraction(self):
        assert _to_hub_datetime("2026-09-07T17:24:51.5Z") == "2026-09-07T17:24:51.500Z"

    def test_explicit_utc_offset(self):
        assert _to_hub_datetime("2026-09-07T17:24:51+00:00") == "2026-09-07T17:24:51.000Z"

    def test_none_passthrough(self):
        assert _to_hub_datetime(None) is None

    def test_empty_passthrough(self):
        assert _to_hub_datetime("") is None


# ======================================================================
# handle_youtube_logic - YouTube captures live in life-data, not Notion
# ======================================================================
class TestYouTubeToLifeData:
    SNIPPET = {
        "items": [
            {
                "id": "dQw4w9WgXcQ",
                "snippet": {
                    "title": "Never Gonna Give You Up",
                    "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
                    "channelTitle": "Rick Astley",
                    "publishedAt": "2009-10-25T06:57:33Z",
                },
                "contentDetails": {"duration": "PT3M33S"},
            }
        ]
    }
    CHANNEL = {
        "items": [
            {
                "id": "UCuAXFkgsw1L7xaCfnd5JJOw",
                "snippet": {"title": "Rick Astley", "customUrl": "@rickastleyyt"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUuAXFkgsw1L7xaCfnd5JJOw"}},
            }
        ]
    }

    def _yt(self, channel_known):
        yt = MagicMock()
        yt.videos().list().execute.return_value = self.SNIPPET
        yt.channels().list().execute.return_value = self.CHANNEL
        return yt

    def test_new_channel_and_video_are_pushed(self, mock_notion):
        with (
            patch("core.handlers.get_youtube", return_value=self._yt(False)),
            patch("core.handlers.known_channel_ids", return_value=set()),
            patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []}) as push,
        ):
            ref = handle_youtube_logic(
                "youtube-videos",
                {
                    "Video URL": "https://youtu.be/dQw4w9WgXcQ?si=abc",
                    "Status": "Not Started",
                    "Tags": ["Classic"],
                },
            )
        assert ref == "youtube_videos/dQw4w9WgXcQ"
        tables = [c.args[0] for c in push.call_args_list]
        assert tables == ["youtube_channels", "youtube_videos"]
        chan = push.call_args_list[0].args[1][0]
        assert chan["id"] == "UCuAXFkgsw1L7xaCfnd5JJOw" and chan["follow"] == 0
        assert chan["backfilled"] == 0
        assert chan["uploads_playlist_id"] == "UUuAXFkgsw1L7xaCfnd5JJOw"
        assert chan["subscription"] == "Never Subscribed"
        vid = push.call_args_list[1].args[1][0]
        assert vid["id"] == "dQw4w9WgXcQ" and vid["channel_id"] == chan["id"]
        assert vid["status"] == "Not Started" and vid["tags"] == ["Classic"]
        assert vid["duration_s"] == 213 and vid["is_short"] == 0
        assert vid["published_at"] == "2009-10-25T06:57:33.000Z"
        mock_notion.pages.create.assert_called_once()  # the "Classify new Channel" cleanup task
        name = sent_props(mock_notion.pages.create, "tasks")["Name"]["title"][0]["text"]["content"]
        assert "Rick Astley" in name

    def test_known_channel_pushes_only_the_video(self):
        with (
            patch("core.handlers.get_youtube", return_value=self._yt(True)),
            patch("core.handlers.known_channel_ids", return_value={"UCuAXFkgsw1L7xaCfnd5JJOw"}),
            patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []}) as push,
        ):
            handle_youtube_logic(
                "youtube-videos",
                {"Video URL": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Status": "Finished"},
            )
        assert [c.args[0] for c in push.call_args_list] == ["youtube_videos"]
        assert push.call_args.args[1][0]["status"] == "Finished"

    def test_no_video_id_raises(self):
        with pytest.raises(ValueError):
            handle_youtube_logic(
                "youtube-videos", {"Video URL": "https://www.youtube.com/@fireship"}
            )

    def test_rejected_push_files_cleanup_task_and_fails(self, mock_notion):
        with (
            patch("core.handlers.get_youtube", return_value=self._yt(True)),
            patch("core.handlers.known_channel_ids", return_value={"UCuAXFkgsw1L7xaCfnd5JJOw"}),
            patch(
                "core.handlers.push_rows",
                return_value={
                    "upserted": 0,
                    "rejected": [
                        {"id": "dQw4w9WgXcQ", "col": "status", "rule": "select", "message": "bad"}
                    ],
                },
            ),
        ):
            out = handle_youtube_logic(
                "youtube-videos",
                {"Video URL": "https://youtu.be/dQw4w9WgXcQ", "Status": "Not Started"},
            )
        assert isinstance(out, Failed) and "bad" in out.detail

    def test_no_youtube_client_files_cleanup_task_and_fails(self, mock_notion):
        with patch("core.handlers.get_youtube", return_value=None):
            out = handle_youtube_logic(
                "youtube-videos", {"Video URL": "https://youtu.be/dQw4w9WgXcQ"}
            )
        assert isinstance(out, Failed)
        name = sent_props(mock_notion.pages.create, "tasks")["Name"]["title"][0]["text"]["content"]
        assert "dQw4w9WgXcQ" in name

    def test_video_not_found_files_cleanup_task_and_fails(self, mock_notion):
        yt = MagicMock()
        yt.videos().list().execute.return_value = {"items": []}
        with patch("core.handlers.get_youtube", return_value=yt):
            out = handle_youtube_logic(
                "youtube-videos", {"Video URL": "https://youtu.be/dQw4w9WgXcQ"}
            )
        assert isinstance(out, Failed)
        name = sent_props(mock_notion.pages.create, "tasks")["Name"]["title"][0]["text"]["content"]
        assert "dQw4w9WgXcQ" in name

    def test_channel_not_found_files_cleanup_task_and_fails(self, mock_notion):
        yt = self._yt(False)
        yt.channels().list().execute.return_value = {"items": []}
        with (
            patch("core.handlers.get_youtube", return_value=yt),
            patch("core.handlers.known_channel_ids", return_value=set()),
            patch("core.handlers.push_rows") as push,
        ):
            out = handle_youtube_logic(
                "youtube-videos", {"Video URL": "https://youtu.be/dQw4w9WgXcQ"}
            )
        assert isinstance(out, Failed)
        assert push.call_count == 0  # no half-written video row without its channel
        name = sent_props(mock_notion.pages.create, "tasks")["Name"]["title"][0]["text"]["content"]
        assert "UCuAXFkgsw1L7xaCfnd5JJOw" in name

    def test_missing_duration_pushes_row_with_no_short_flag(self):
        yt = self._yt(True)
        yt.videos().list().execute.return_value = {
            "items": [{**self.SNIPPET["items"][0], "contentDetails": {}}]
        }
        with (
            patch("core.handlers.get_youtube", return_value=yt),
            patch("core.handlers.known_channel_ids", return_value={"UCuAXFkgsw1L7xaCfnd5JJOw"}),
            patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []}) as push,
        ):
            handle_youtube_logic(
                "youtube-videos",
                {"Video URL": "https://youtu.be/dQw4w9WgXcQ", "Status": "Not Started"},
            )
        vid = push.call_args.args[1][0]
        assert vid["duration_s"] is None and vid["is_short"] == 0


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

    def test_empty_status_falls_back_to_the_default(self):
        """The extractor emits "" for an absent field; a required column must
        never go out empty."""
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="27205"),
            patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []}) as push,
        ):
            handle_movies_tv_logic("movies", {"Title": "Inception", "Status": ""})
        assert push.call_args.args[1][0]["status"] == "Not Started"

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
            out = handle_movies_tv_logic(
                "movies", {"Title": "Some Obscure Film", "Status": "Priority"}
            )

        push.assert_not_called()
        # nothing was written: the pipeline must not log this as a Success
        assert isinstance(out, Failed)
        assert "Some Obscure Film" in out.detail
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
            out = handle_movies_tv_logic("movies", {"Title": "Inception", "Status": "Bogus"})

        assert isinstance(out, Failed)
        assert rejected["message"] in out.detail
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
