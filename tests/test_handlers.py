"""Tests for handlers.py — category-specific logic for the Soma-backed categories."""

from core import soma_hub


import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.handlers import (
    Failed,
    _to_hub_datetime,
    handle_hub_logic,
    handle_youtube_logic,
    handle_movies_tv_logic,
)
from core.config import CATEGORIES


@pytest.fixture
def cleanup():
    """The cleanup task a handler files (a Soma task row in a real capture)."""
    with patch("core.handlers.create_cleanup_task") as filed:
        yield filed


# ======================================================================
# handle_hub_logic - every category stanza with a table + columns
# ======================================================================
class TestHubHandler:
    """New rows are inserted; a matched row gets a conditional patch at the
    revision it was read at (no category write is an unconditional upsert)."""

    REVISION = {"updated_at": "2026-01-01T00:00:00.000Z", "hub_at": "2026-01-01T00:00:01.000Z"}

    @pytest.fixture
    def writes(self):
        with (
            patch("core.handlers.insert_rows", return_value={"inserted": ["x"]}) as insert,
            patch("core.handlers.patch_row", return_value={"id": "x"}) as patch_,
        ):
            yield SimpleNamespace(insert=insert, patch=patch_)

    def _known(self, **cols):
        return patch("core.handlers.pull_rows", return_value=[{**self.REVISION, **cols}])

    def test_new_grocery_gets_a_fresh_id_and_only_known_columns(self, writes):
        with patch("core.handlers.pull_rows", return_value=[]):
            ref = handle_hub_logic(
                "groceries", {"Name": "Quinoa", "Category": "Grains", "Status": "On List"}
            )
        table, rows = writes.insert.call_args.args
        row = rows[0]
        assert table == "groceries" and ref == f"groceries/{row['id']}"
        assert len(row["id"]) == 32
        assert row["name"] == "Quinoa" and row["category"] == "Grains"
        assert row["status"] == "On List"
        assert "notes" not in row  # never sends a column it does not know
        writes.patch.assert_not_called()

    def test_existing_grocery_is_patched_by_name_case_insensitively(self, writes):
        with self._known(id="abc", name="Eggs") as pull:
            ref = handle_hub_logic("groceries", {"Name": "eggs", "Status": "Have"})
        assert ref == "groceries/abc"
        assert pull.call_args.args == ("groceries", ["name", "updated_at", "hub_at"])
        table, row_id, values, revision = writes.patch.call_args.args
        assert (table, row_id, revision) == ("groceries", "abc", self.REVISION)
        assert values == {"name": "eggs", "status": "Have"}  # never clobbers the rest
        writes.insert.assert_not_called()

    def test_constants_and_review_reason(self, writes):
        with patch("core.handlers.pull_rows") as pull:
            handle_hub_logic("fun-activities", {"Title": "Go Kayaking", "Status": "Someday"})
        pull.assert_not_called()  # no match_on -> no lookup
        table, rows = writes.insert.call_args.args
        assert table == "things_to_do"
        assert rows[0]["kind"] == "Activity" and rows[0]["title"] == "Go Kayaking"
        assert "City" in rows[0]["needs_review"]

    def test_bucket_list_is_an_ambition(self, writes):
        handle_hub_logic("bucket-list", {"Item": "Skydive in Dubai", "Tags": ["Adventure"]})
        row = writes.insert.call_args.args[1][0]
        assert row["kind"] == "Ambition" and row["tags"] == ["Adventure"]

    def test_rejected_insert_files_a_cleanup_task_and_fails(self, cleanup):
        rejected = soma_hub.InsertRejected(
            "Soma rejected 1 rows: x: category required (category is required.)"
        )
        with (
            patch("core.handlers.pull_rows", return_value=[]),
            patch("core.handlers.insert_rows", side_effect=rejected),
        ):
            out = handle_hub_logic("groceries", {"Name": "Quinoa", "Status": "On List"})
        assert isinstance(out, Failed) and "category is required" in out.detail
        cleanup.assert_called_once()
        name = cleanup.call_args.args[0]
        assert "Quinoa" in name and "rejected" in name

    @pytest.mark.parametrize("status", [409, 422])
    def test_a_rejected_or_conflicting_patch_files_a_cleanup_task_and_fails(self, cleanup, status):
        error = requests.HTTPError(response=SimpleNamespace(status_code=status))
        with (
            self._known(id="abc", name="Eggs"),
            patch("core.handlers.patch_row", side_effect=error),
        ):
            out = handle_hub_logic("groceries", {"Name": "eggs", "Status": "Have"})
        assert isinstance(out, Failed) and "groceries/abc" in out.detail
        cleanup.assert_called_once()

    def test_an_unavailable_hub_is_raised_for_a_retry(self, cleanup):
        error = requests.HTTPError(response=SimpleNamespace(status_code=503))
        with (
            self._known(id="abc", name="Eggs"),
            patch("core.handlers.patch_row", side_effect=error),
        ):
            with pytest.raises(requests.HTTPError):
                handle_hub_logic("groceries", {"Name": "eggs", "Status": "Have"})
        cleanup.assert_not_called()

    def test_bookmark_matches_on_url(self, writes):
        with self._known(id="bm1", url="https://example.com"):
            ref = handle_hub_logic(
                "bookmarks", {"Description": "A site", "URL": "https://example.com", "Tags": []}
            )
        assert ref == "bookmarks/bm1"
        values = writes.patch.call_args.args[2]
        assert values["description"] == "A site"
        assert "tags" not in values  # empty lists are not sent

    # A bookmark whose page could not be fetched: the pipeline drops Title and
    # marks Description/Tags as guesses that may only fill empty columns.
    GUESS = {
        "Description": "A guess",
        "URL": "https://x.com",
        "Tags": ["Money"],
        "_fill_only": ["Description", "Tags"],
    }
    REASON = CATEGORIES["bookmarks"]["review_if_missing"]["Title"]

    def _bookmark(self, **cols):
        return self._known(
            **{"id": "bm1", "url": "https://x.com", "title": None, "description": None, **cols}
        )

    def test_a_guess_never_replaces_a_known_bookmarks_values(self, writes):
        known = self._bookmark(title="X", description="Real", tags='["List"]', needs_review=None)
        with known as pull:
            ref = handle_hub_logic("bookmarks", dict(self.GUESS))
        assert ref == "bookmarks/bm1"
        assert set(pull.call_args.args[1]) >= {"url", "title", "description", "tags"}
        values = writes.patch.call_args.args[2]
        assert "description" not in values and "tags" not in values
        assert "needs_review" not in values  # the row already has the title we could not fetch

    def test_a_guess_still_fills_an_empty_column_on_a_known_bookmark(self, writes):
        with self._bookmark(description="Real", tags="[]", needs_review=None):
            handle_hub_logic("bookmarks", dict(self.GUESS))
        values = writes.patch.call_args.args[2]
        assert values["tags"] == ["Money"] and "description" not in values
        assert values["needs_review"] == self.REASON  # still no title anywhere

    def test_filling_the_flagged_column_clears_its_own_reason(self, writes):
        with self._bookmark(needs_review=self.REASON):
            handle_hub_logic(
                "bookmarks", {"Description": "Real", "Title": "X", "URL": "https://x.com"}
            )
        values = writes.patch.call_args.args[2]
        assert values["title"] == "X" and values["needs_review"] is None

    def test_another_review_reason_is_left_alone(self, writes):
        with self._bookmark(needs_review="Duplicate of another bookmark"):
            handle_hub_logic(
                "bookmarks", {"Description": "Real", "Title": "X", "URL": "https://x.com"}
            )
        assert "needs_review" not in writes.patch.call_args.args[2]


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

    def test_new_channel_and_video_are_pushed(self, cleanup):
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
        cleanup.assert_called_once()  # the "Classify new Channel" cleanup task
        name = cleanup.call_args.args[0]
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

    def test_no_video_id_files_cleanup_task_and_fails(self, cleanup):
        """Deterministic, nothing written: a retry could never succeed."""
        out = handle_youtube_logic(
            "youtube-videos", {"Video URL": "https://www.youtube.com/@fireship"}
        )
        assert isinstance(out, Failed) and "No YouTube video ID" in out.detail
        assert "youtube.com/@fireship" in cleanup.call_args.args[0]

    def test_rejected_push_files_cleanup_task_and_fails(self, cleanup):
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

    def test_no_youtube_client_files_cleanup_task_and_fails(self, cleanup):
        with patch("core.handlers.get_youtube", return_value=None):
            out = handle_youtube_logic(
                "youtube-videos", {"Video URL": "https://youtu.be/dQw4w9WgXcQ"}
            )
        assert isinstance(out, Failed)
        name = cleanup.call_args.args[0]
        assert "dQw4w9WgXcQ" in name

    def test_video_not_found_files_cleanup_task_and_fails(self, cleanup):
        yt = MagicMock()
        yt.videos().list().execute.return_value = {"items": []}
        with patch("core.handlers.get_youtube", return_value=yt):
            out = handle_youtube_logic(
                "youtube-videos", {"Video URL": "https://youtu.be/dQw4w9WgXcQ"}
            )
        assert isinstance(out, Failed)
        name = cleanup.call_args.args[0]
        assert "dQw4w9WgXcQ" in name

    def test_channel_not_found_files_cleanup_task_and_fails(self, cleanup):
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
        name = cleanup.call_args.args[0]
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
    def test_confident_match_pushes_one_row(self, cleanup):
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
        cleanup.assert_not_called()

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

    def test_no_tmdb_match_files_cleanup_task_and_pushes_nothing(self, cleanup):
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
        cleanup.assert_called_once()
        name = cleanup.call_args.args[0]
        assert "Some Obscure Film" in name and "TMDB" in name

    def test_rejected_row_files_cleanup_task_with_the_rule_message(self, cleanup):
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
        cleanup.assert_called_once()
        name = cleanup.call_args.args[0]
        assert "insert_rejected" in name
