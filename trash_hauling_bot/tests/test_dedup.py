import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.dedup import DedupStore


class TestFingerprint:
    def test_same_inputs_produce_same_hash(self):
        fp1 = DedupStore.fingerprint("https://facebook.com/marketplace/item/1/", "title")
        fp2 = DedupStore.fingerprint("https://facebook.com/marketplace/item/1/", "title")
        assert fp1 == fp2

    def test_different_urls_produce_different_hashes(self):
        fp1 = DedupStore.fingerprint("https://facebook.com/marketplace/item/1/")
        fp2 = DedupStore.fingerprint("https://facebook.com/marketplace/item/2/")
        assert fp1 != fp2

    def test_case_insensitive_url(self):
        fp1 = DedupStore.fingerprint("https://FACEBOOK.COM/Marketplace/Item/1/")
        fp2 = DedupStore.fingerprint("https://facebook.com/marketplace/item/1/")
        assert fp1 == fp2

    def test_whitespace_stripped(self):
        fp1 = DedupStore.fingerprint("  https://facebook.com/marketplace/item/1/  ")
        fp2 = DedupStore.fingerprint("https://facebook.com/marketplace/item/1/")
        assert fp1 == fp2

    def test_hash_length(self):
        fp = DedupStore.fingerprint("https://facebook.com/marketplace/item/1/")
        assert len(fp) == 20


class TestDedupStore:
    def test_new_url_not_seen(self):
        with tempfile.TemporaryDirectory() as d:
            store = DedupStore(os.path.join(d, "seen.json"))
            assert not store.is_seen("https://facebook.com/marketplace/item/999/")

    def test_mark_then_seen(self):
        with tempfile.TemporaryDirectory() as d:
            store = DedupStore(os.path.join(d, "seen.json"))
            url = "https://facebook.com/marketplace/item/42/"
            store.mark_seen(url)
            assert store.is_seen(url)

    def test_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "seen.json")
            store = DedupStore(path)
            store.mark_seen("https://facebook.com/marketplace/item/1/")
            # New instance loads from disk
            store2 = DedupStore(path)
            assert store2.is_seen("https://facebook.com/marketplace/item/1/")

    def test_different_url_still_unseen_after_mark(self):
        with tempfile.TemporaryDirectory() as d:
            store = DedupStore(os.path.join(d, "seen.json"))
            store.mark_seen("https://facebook.com/marketplace/item/1/")
            assert not store.is_seen("https://facebook.com/marketplace/item/2/")

    def test_json_file_contains_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "seen.json")
            store = DedupStore(path)
            store.mark_seen("https://facebook.com/marketplace/item/1/")
            with open(path) as f:
                data = json.load(f)
            assert isinstance(data, list)
            assert len(data) == 1

    def test_handles_corrupt_store_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "seen.json")
            with open(path, "w") as f:
                f.write("not valid json {{{")
            store = DedupStore(path)  # should not raise
            assert not store.is_seen("https://facebook.com/marketplace/item/1/")
