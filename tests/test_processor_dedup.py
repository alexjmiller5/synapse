"""Tests for processor duplicate detection logic."""

import time
import unittest
from collections import OrderedDict


# --- Replicate processor dedup logic for isolated testing ---

DEDUP_WINDOW_SECONDS = 600
DEDUP_MAX_SIZE = 50


def _evict_expired(cache, window_seconds):
    now = time.time()
    while cache:
        oldest_key, oldest_time = next(iter(cache.items()))
        if now - oldest_time > window_seconds:
            cache.pop(oldest_key)
        else:
            break


def _is_duplicate_message(cache, key, window_seconds=600, max_size=50):
    now = time.time()
    _evict_expired(cache, window_seconds)

    if key in cache:
        return True

    cache[key] = now

    while len(cache) > max_size:
        cache.popitem(last=False)

    return False


class TestProcessorDedup(unittest.TestCase):
    def setUp(self):
        self.cache = OrderedDict()

    def test_first_message_id_passes(self):
        """First occurrence of a message_id should pass through."""
        result = _is_duplicate_message(self.cache, "mid:msg-001")
        self.assertFalse(result)

    def test_same_message_id_is_duplicate(self):
        """Same message_id sent again should be caught."""
        _is_duplicate_message(self.cache, "mid:msg-001")
        result = _is_duplicate_message(self.cache, "mid:msg-001")
        self.assertTrue(result)

    def test_different_message_ids_pass(self):
        """Different message_ids should both pass."""
        _is_duplicate_message(self.cache, "mid:msg-001")
        result = _is_duplicate_message(self.cache, "mid:msg-002")
        self.assertFalse(result)

    def test_thought_id_dedup(self):
        """thought_id-based dedup should work independently."""
        _is_duplicate_message(self.cache, "tid:abc-123")
        result = _is_duplicate_message(self.cache, "tid:abc-123")
        self.assertTrue(result)

    def test_message_id_and_thought_id_independent(self):
        """message_id and thought_id with same value but different prefix are independent."""
        _is_duplicate_message(self.cache, "mid:same-value")
        result = _is_duplicate_message(self.cache, "tid:same-value")
        self.assertFalse(result)

    def test_expired_entries_evicted(self):
        """Entries older than window should be evicted."""
        past_time = time.time() - 700  # 700s ago, window is 600s
        self.cache["mid:old-msg"] = past_time

        # New message should cause eviction of old
        result = _is_duplicate_message(self.cache, "mid:new-msg")
        self.assertFalse(result)
        self.assertNotIn("mid:old-msg", self.cache)

    def test_cache_size_limit(self):
        """Cache should respect max_size limit."""
        max_size = 5
        for i in range(10):
            _is_duplicate_message(self.cache, f"mid:msg-{i}", max_size=max_size)
        self.assertLessEqual(len(self.cache), max_size)

    def test_oldest_evicted_first(self):
        """Oldest entries should be evicted when cache overflows."""
        max_size = 3
        _is_duplicate_message(self.cache, "mid:first", max_size=max_size)
        _is_duplicate_message(self.cache, "mid:second", max_size=max_size)
        _is_duplicate_message(self.cache, "mid:third", max_size=max_size)
        _is_duplicate_message(self.cache, "mid:fourth", max_size=max_size)

        self.assertNotIn("mid:first", self.cache)
        self.assertIn("mid:fourth", self.cache)

    def test_redelivered_after_eviction_passes(self):
        """A message that was evicted should pass through again."""
        max_size = 2
        _is_duplicate_message(self.cache, "mid:msg-1", max_size=max_size)
        _is_duplicate_message(self.cache, "mid:msg-2", max_size=max_size)
        _is_duplicate_message(self.cache, "mid:msg-3", max_size=max_size)

        # msg-1 was evicted, so it should pass through again
        result = _is_duplicate_message(self.cache, "mid:msg-1", max_size=max_size)
        self.assertFalse(result)

    def test_two_layer_defense(self):
        """Both message_id and thought_id should be independently tracked."""
        # message_id catches Pub/Sub redeliveries
        _is_duplicate_message(self.cache, "mid:pubsub-msg-1")
        self.assertTrue(_is_duplicate_message(self.cache, "mid:pubsub-msg-1"))

        # thought_id catches intaker double-publish
        _is_duplicate_message(self.cache, "tid:thought-uuid-1")
        self.assertTrue(_is_duplicate_message(self.cache, "tid:thought-uuid-1"))

        # They don't interfere with each other
        self.assertEqual(len(self.cache), 2)


if __name__ == "__main__":
    unittest.main()
