"""Tests for handlers.py — category-specific logic for all Notion DB categories."""

from core import soma_hub


import re
from unittest.mock import MagicMock, patch

import pytest

from core.handlers import (
    Failed,
    _to_hub_datetime,
    handle_hub_logic,
    handle_youtube_logic,
    handle_movies_tv_logic,
    handle_default_logic,
)
from core.config import DATABASES
from helpers import sent_props


# ======================================================================
# handle_hub_logic - every yaml stanza with hub_table + columns
# ======================================================================
class TestHubHandler:
    def _push(self):
        return patch("core.handlers.push_rows", return_value={"upserted": 1, "rejected": []})

    def test_new_grocery_gets_a_fresh_id_and_only_known_columns(self):
        with (
            patch("core.handlers.pull_rows", return_value=[]),
            self._push() as push,
        ):
            ref = handle_hub_logic(
                "groceries", {"Name": "Quinoa", "Category": "Grains", "Status": "On List"}
            )
        table, rows = push.call_args.args
        row = rows[0]
        assert table == "groceries" and ref == f"groceries/{row['id']}"
        assert len(row["id"]) == 32
        assert row["name"] == "Quinoa" and row["category"] == "Grains"
        assert row["status"] == "On List" and row["updated_at"].endswith("Z")
        assert "notes" not in row  # never sends a column it does not know

    def test_existing_grocery_is_updated_by_name_case_insensitively(self):
        with (
            patch("core.handlers.pull_rows", return_value=[{"id": "abc", "name": "Eggs"}]) as pull,
            self._push() as push,
        ):
            ref = handle_hub_logic("groceries", {"Name": "eggs", "Status": "Have"})
        assert ref == "groceries/abc"
        assert pull.call_args.args == ("groceries", ["name"])
        row = push.call_args.args[1][0]
        assert row["id"] == "abc" and row["status"] == "Have"
        assert "category" not in row  # a status capture never clobbers the rest

    def test_constants_and_review_reason(self):
        with patch("core.handlers.pull_rows") as pull, self._push() as push:
            handle_hub_logic("fun-activities", {"Title": "Go Kayaking", "Status": "Someday"})
        pull.assert_not_called()  # no match_on -> no lookup
        row = push.call_args.args[1][0]
        assert push.call_args.args[0] == "things_to_do"
        assert row["kind"] == "Activity" and row["title"] == "Go Kayaking"
        assert "City" in row["needs_review"]

    def test_bucket_list_is_an_ambition(self):
        with self._push() as push:
            handle_hub_logic("bucket-list", {"Item": "Skydive in Dubai", "Tags": ["Adventure"]})
        row = push.call_args.args[1][0]
        assert row["kind"] == "Ambition" and row["tags"] == ["Adventure"]

    def test_rejected_row_files_a_cleanup_task_and_fails(self, mock_notion):
        with (
            patch("core.handlers.pull_rows", return_value=[]),
            patch(
                "core.handlers.push_rows",
                return_value={
                    "rejected": [{"col": "category", "message": "category is required."}]
                },
            ),
        ):
            out = handle_hub_logic("groceries", {"Name": "Quinoa", "Status": "On List"})
        assert isinstance(out, Failed) and "category is required" in out.detail
        mock_notion.pages.create.assert_called_once()
        name = sent_props(mock_notion.pages.create, "tasks")["Name"]["title"][0]["text"]["content"]
        assert "Quinoa" in name and "rejected" in name

    def test_bookmark_matches_on_url(self):
        with (
            patch(
                "core.handlers.pull_rows",
                return_value=[{"id": "bm1", "url": "https://example.com"}],
            ),
            self._push() as push,
        ):
            ref = handle_hub_logic(
                "bookmarks", {"Description": "A site", "URL": "https://example.com", "Tags": []}
            )
        assert ref == "bookmarks/bm1"
        row = push.call_args.args[1][0]
        assert row["id"] == "bm1" and row["description"] == "A site"
        assert "tags" not in row  # empty lists are not sent

    # A bookmark whose page could not be fetched: the pipeline drops Title and
    # marks Description/Tags as guesses that may only fill empty columns.
    GUESS = {
        "Description": "A guess",
        "URL": "https://x.com",
        "Tags": ["Money"],
        "_fill_only": ["Description", "Tags"],
    }
    REASON = DATABASES["databases"]["bookmarks"]["review_if_missing"]["Title"]

    def _known(self, **cols):
        row = {"id": "bm1", "url": "https://x.com", "title": None, "description": None}
        return patch("core.handlers.pull_rows", return_value=[{**row, **cols}])

    def test_a_guess_never_replaces_a_known_bookmarks_values(self):
        known = self._known(title="X", description="Real", tags='["List"]', needs_review=None)
        with known as pull, self._push() as push:
            ref = handle_hub_logic("bookmarks", dict(self.GUESS))
        assert ref == "bookmarks/bm1"
        assert set(pull.call_args.args[1]) >= {"url", "title", "description", "tags"}
        row = push.call_args.args[1][0]
        assert row["id"] == "bm1"
        assert "description" not in row and "tags" not in row
        assert "needs_review" not in row  # the row already has the title we could not fetch

    def test_a_guess_still_fills_an_empty_column_on_a_known_bookmark(self):
        with self._known(description="Real", tags="[]", needs_review=None), self._push() as push:
            handle_hub_logic("bookmarks", dict(self.GUESS))
        row = push.call_args.args[1][0]
        assert row["tags"] == ["Money"] and "description" not in row
        assert row["needs_review"] == self.REASON  # still no title anywhere

    def test_filling_the_flagged_column_clears_its_own_reason(self):
        with self._known(needs_review=self.REASON), self._push() as push:
            handle_hub_logic(
                "bookmarks", {"Description": "Real", "Title": "X", "URL": "https://x.com"}
            )
        row = push.call_args.args[1][0]
        assert row["title"] == "X" and row["needs_review"] is None

    def test_another_review_reason_is_left_alone(self):
        with self._known(needs_review="Duplicate of another bookmark"), self._push() as push:
            handle_hub_logic(
                "bookmarks", {"Description": "Real", "Title": "X", "URL": "https://x.com"}
            )
        assert "needs_review" not in push.call_args.args[1][0]


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
# handle_youtube_logic - YouTube captures live in soma, not Notion
# ======================================================================
@pytest.mark.usefixtures("media_hub")
class TestYouTubeToSomaData:
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
            patch("core.soma_hub.insert_rows", wraps=soma_hub.insert_rows) as push,
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
        assert "subscription" not in chan
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
            patch("core.soma_hub.insert_rows", wraps=soma_hub.insert_rows) as push,
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
                "core.soma_hub.insert_rows",
                return_value={
                    "inserted": [],
                    "existing": [],
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
        assert isinstance(out, Failed) and "insert_rejected" in out.detail

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
            patch("core.soma_hub.insert_rows") as push,
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
            patch("core.soma_hub.insert_rows", wraps=soma_hub.insert_rows) as push,
        ):
            handle_youtube_logic(
                "youtube-videos",
                {"Video URL": "https://youtu.be/dQw4w9WgXcQ", "Status": "Not Started"},
            )
        vid = push.call_args.args[1][0]
        assert vid["duration_s"] is None and vid["is_short"] == 0


# ======================================================================
# handle_movies_tv_logic - movies/TV live in soma, not Notion
# ======================================================================
ISO_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.mark.usefixtures("media_hub")
class TestHandleMoviesTv:
    def test_confident_match_pushes_one_row(self, mock_notion):
        data = {"Title": "Inception", "Status": "Not Started", "Tags": ["Favorite"]}
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="27205") as resolve,
            patch("core.soma_hub.insert_rows", wraps=soma_hub.insert_rows) as push,
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
        assert row["tags"] == ["Favorite"]
        assert ISO_MS.match(row["updated_at"])
        # Created Item is the soma row reference, not a Notion URL
        assert ref == "movies/27205"
        # Nothing goes to Notion for these categories any more
        mock_notion.pages.create.assert_not_called()
        mock_notion.pages.update.assert_not_called()

    def test_tags_omitted_when_not_extracted(self):
        """Push only the columns you have - the hub upsert touches only those, so a
        status update must not blank an existing row's tags."""
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="27205"),
            patch("core.soma_hub.insert_rows", wraps=soma_hub.insert_rows) as push,
        ):
            handle_movies_tv_logic("movies", {"Title": "Inception", "Status": "Finished"})
        assert set(push.call_args.args[1][0]) == {"id", "status", "updated_at"}

    def test_empty_status_falls_back_to_the_default(self):
        """The extractor emits "" for an absent field; a required column must
        never go out empty."""
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="27205"),
            patch("core.soma_hub.insert_rows", wraps=soma_hub.insert_rows) as push,
        ):
            handle_movies_tv_logic("movies", {"Title": "Inception", "Status": ""})
        assert push.call_args.args[1][0]["status"] == "Not Started"

    def test_tv_shows_push_to_tv_shows_table(self):
        with (
            patch("core.handlers.resolve_tmdb_id", return_value="1396") as resolve,
            patch("core.soma_hub.insert_rows", wraps=soma_hub.insert_rows) as push,
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
            patch("core.soma_hub.insert_rows") as push,
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
            patch(
                "core.soma_hub.insert_rows",
                return_value={"inserted": [], "existing": [], "rejected": [rejected]},
            ),
        ):
            out = handle_movies_tv_logic("movies", {"Title": "Inception", "Status": "Bogus"})

        assert isinstance(out, Failed)
        assert "insert_rejected" in out.detail
        mock_notion.pages.create.assert_called_once()
        name = sent_props(mock_notion.pages.create, "tasks")["Name"]["title"][0]["text"]["content"]
        assert "insert_rejected" in name


# ======================================================================
# handle_default_logic
# ======================================================================
class TestHandleDefault:
    def test_creates_page(self, mock_notion):
        data = {"Description": "Random idea", "Tags": ["Tech"]}
        handle_default_logic("ideas", data)
        mock_notion.pages.create.assert_called_once()
