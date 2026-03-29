"""Tests for intaker duplicate detection logic.

Tests the dedup logic in isolation by replicating the core functions.
The intaker module has GCP side effects at import time, so we test
the algorithm directly rather than importing the module.
"""

import hashlib
import time
import unittest
from collections import OrderedDict


def _make_text_key(raw_text):
    normalized = raw_text.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _evict_expired(cache, window_seconds):
    now = time.time()
    while cache:
        oldest_key, oldest_time = next(iter(cache.items()))
        if now - oldest_time > window_seconds:
            cache.pop(oldest_key)
        else:
            break


def _is_duplicate(cache, thought_id, raw_text, window_seconds=300, max_size=100):
    now = time.time()
    _evict_expired(cache, window_seconds)

    if thought_id:
        key = f"tid:{thought_id}"
        if key in cache:
            return True
        cache[key] = now
    else:
        key = f"txt:{_make_text_key(raw_text)}"
        if key in cache:
            return True
        cache[key] = now

    while len(cache) > max_size:
        cache.popitem(last=False)

    return False


class TestIntakerDedup(unittest.TestCase):
    def setUp(self):
        self.cache = OrderedDict()

    def test_first_message_passes_through(self):
        """First-time message should not be flagged as duplicate."""
        result = _is_duplicate(self.cache, None, "Buy eggs")
        self.assertFalse(result)

    def test_same_text_within_window_is_duplicate(self):
        """Same text sent twice within the window should be caught."""
        _is_duplicate(self.cache, None, "Buy eggs")
        result = _is_duplicate(self.cache, None, "Buy eggs")
        self.assertTrue(result)

    def test_same_text_case_insensitive(self):
        """Dedup should be case-insensitive."""
        _is_duplicate(self.cache, None, "Buy Eggs")
        result = _is_duplicate(self.cache, None, "buy eggs")
        self.assertTrue(result)

    def test_same_text_whitespace_normalized(self):
        """Dedup should normalize leading/trailing whitespace."""
        _is_duplicate(self.cache, None, "  Buy eggs  ")
        result = _is_duplicate(self.cache, None, "Buy eggs")
        self.assertTrue(result)

    def test_different_text_passes_through(self):
        """Different text should not be flagged."""
        _is_duplicate(self.cache, None, "Buy eggs")
        result = _is_duplicate(self.cache, None, "Buy milk")
        self.assertFalse(result)

    def test_thought_id_dedup(self):
        """Same thought_id should be caught regardless of text."""
        tid = "abc-123"
        _is_duplicate(self.cache, tid, "Buy eggs")
        result = _is_duplicate(self.cache, tid, "Buy milk")  # different text, same ID
        self.assertTrue(result)

    def test_different_thought_id_same_text_passes(self):
        """Different thought_id with same text should pass (intentional repeat)."""
        _is_duplicate(self.cache, "id-1", "Buy eggs")
        result = _is_duplicate(self.cache, "id-2", "Buy eggs")
        self.assertFalse(result)

    def test_thought_id_takes_priority_over_text(self):
        """When thought_id is present, text-based dedup should not be used."""
        # Send with thought_id
        _is_duplicate(self.cache, "id-1", "Buy eggs")
        # Send same text without thought_id — should pass because
        # the first entry was keyed by thought_id, not text
        result = _is_duplicate(self.cache, None, "Buy eggs")
        self.assertFalse(result)

    def test_expired_entries_evicted(self):
        """Entries older than the window should be evicted."""
        # Insert with a timestamp in the past
        past_time = time.time() - 400  # 400s ago, window is 300s
        self.cache["txt:old-hash"] = past_time

        # This should evict the old entry and pass through
        result = _is_duplicate(self.cache, None, "Fresh text")
        self.assertFalse(result)
        self.assertNotIn("txt:old-hash", self.cache)

    def test_cache_size_limit(self):
        """Cache should not exceed max_size."""
        max_size = 5
        for i in range(10):
            _is_duplicate(self.cache, None, f"Message {i}", max_size=max_size)
        self.assertLessEqual(len(self.cache), max_size)

    def test_oldest_evicted_on_overflow(self):
        """When cache overflows, oldest entries should be removed first."""
        max_size = 3
        _is_duplicate(self.cache, None, "First", max_size=max_size)
        _is_duplicate(self.cache, None, "Second", max_size=max_size)
        _is_duplicate(self.cache, None, "Third", max_size=max_size)
        _is_duplicate(self.cache, None, "Fourth", max_size=max_size)

        # "First" should have been evicted
        first_key = f"txt:{_make_text_key('First')}"
        self.assertNotIn(first_key, self.cache)

        # "Fourth" should still be there
        fourth_key = f"txt:{_make_text_key('Fourth')}"
        self.assertIn(fourth_key, self.cache)

    def test_no_thought_id_fallback_to_text(self):
        """When thought_id is None, should fall back to text-based dedup."""
        _is_duplicate(self.cache, None, "Buy eggs")
        result = _is_duplicate(self.cache, None, "Buy eggs")
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
