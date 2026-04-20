import hashlib
import json
import logging
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)


class DedupStore:
    def __init__(self, store_file: str = "data/seen_listings.json"):
        self._path = Path(store_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: Set[str] = self._load()

    def _load(self) -> Set[str]:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    return set(json.load(f))
            except (json.JSONDecodeError, OSError):
                return set()
        return set()

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(list(self._seen), f)
        except OSError as exc:
            logger.error("Dedup store save failed: %s", exc)

    @staticmethod
    def fingerprint(listing_url: str, title: str = "") -> str:
        raw = f"{listing_url.strip().lower()}|{title.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    def is_seen(self, listing_url: str, title: str = "") -> bool:
        return self.fingerprint(listing_url, title) in self._seen

    def mark_seen(self, listing_url: str, title: str = "") -> None:
        self._seen.add(self.fingerprint(listing_url, title))
        self._save()
