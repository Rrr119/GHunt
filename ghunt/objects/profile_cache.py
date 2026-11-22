import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ghunt.objects.encoders import GHuntEncoder


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProfileCache:
    """
    On-disk incremental cache for Google account profiles.

    One JSON file per target key (email or Gaia ID). Every fetch is merged
    field-by-field into the latest snapshot, previous values are kept in the
    history, and only the fields that actually changed are reported.

    The stored schema is versioned (``schema_version``) so future GHunt
    releases can migrate older caches without breaking them.
    """

    SCHEMA_VERSION = 1

    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is None:
            cache_dir = Path().home() / ".malfrats" / "ghunt" / "profile_cache"
        self.cache_dir = Path(cache_dir)

    def _entry_path(self, target: str) -> Path:
        safe_name = f"{target.lower().strip()}.json"
        return self.cache_dir / safe_name

    def load(self, target: str) -> Optional[Dict[str, Any]]:
        """Returns the cached entry for a target, or None if there is none."""
        path = self._entry_path(target)
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if entry.get("schema_version") != self.SCHEMA_VERSION:
            return None
        return entry

    def is_fresh(self, target: str, ttl: Optional[int]) -> bool:
        """True when a non-expired cached snapshot exists for the target."""
        if not ttl or ttl <= 0:
            return False
        entry = self.load(target)
        if not entry or not entry.get("snapshots"):
            return False
        last_fetched = entry["snapshots"][-1].get("fetched_at", "")
        try:
            last_dt = datetime.strptime(last_fetched, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        age = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return age < ttl

    @staticmethod
    def _diff(old: Any, new: Any, prefix: str = "") -> List[Dict[str, Any]]:
        """Recursively compares two JSON-compatible values, field by field."""
        changes: List[Dict[str, Any]] = []
        if isinstance(new, dict) and isinstance(old, dict):
            for key in sorted(set(new) | set(old)):
                field = f"{prefix}.{key}" if prefix else str(key)
                if key not in old:
                    changes.append({"field": field, "old": None, "new": new[key], "change": "added"})
                elif key not in new:
                    changes.append({"field": field, "old": old[key], "new": None, "change": "removed"})
                else:
                    changes.extend(ProfileCache._diff(old[key], new[key], field))
        elif new is None and old is not None:
            changes.append({"field": prefix, "old": old, "new": None, "change": "removed"})
        elif old != new:
            changes.append({"field": prefix, "old": old, "new": new, "change": "changed"})
        return changes

    @staticmethod
    def _merge(old: Any, new: Any) -> Any:
        """Deep merge: new values win, but data absent from new is retained."""
        if isinstance(new, dict) and isinstance(old, dict):
            merged = deepcopy(old)
            for key, value in new.items():
                if key in merged:
                    merged[key] = ProfileCache._merge(merged[key], value)
                else:
                    merged[key] = deepcopy(value)
            return merged
        return deepcopy(new)

    def save(
        self,
        target: str,
        profile_data: Dict[str, Any],
        source: str = "people_lookup",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Merges a fresh snapshot into the cache and returns the updated entry.

        The returned dict contains ``snapshot`` (merged latest view),
        ``changes`` (fields that differ from the previous snapshot) and
        ``history`` (one compact record per fetch).
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        entry = self.load(target) or {
            "schema_version": self.SCHEMA_VERSION,
            "target": target.lower().strip(),
            "first_seen_at": utcnow_iso(),
            "snapshots": [],
            "history": [],
        }

        previous = entry["snapshots"][-1]["data"] if entry["snapshots"] else {}
        snapshot = self._merge(previous, profile_data) if previous else deepcopy(profile_data)
        changes = self._diff(previous, snapshot) if previous else [
            {"field": "", "old": None, "new": None, "change": "initial"}
        ]

        record = {
            "fetched_at": utcnow_iso(),
            "source": source,
            "changes": changes,
            "data": snapshot,
        }
        if metadata:
            record["metadata"] = metadata
        entry["snapshots"].append(record)
        entry["history"].append({
            "fetched_at": record["fetched_at"],
            "source": source,
            "change_count": len(changes),
            "changed_fields": [change["field"] for change in changes],
        })

        path = self._entry_path(target)
        self._atomic_write(path, entry)
        return entry

    @staticmethod
    def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, cls=GHuntEncoder, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def clear(self, target: Optional[str] = None) -> int:
        """Deletes one target's cache, or the whole cache dir. Returns count."""
        if target:
            path = self._entry_path(target)
            if path.is_file():
                path.unlink()
                return 1
            return 0
        removed = 0
        if self.cache_dir.is_dir():
            for path in self.cache_dir.glob("*.json"):
                path.unlink()
                removed += 1
        return removed

    def get_latest(self, target: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Convenience accessor: (latest merged snapshot, full entry)."""
        entry = self.load(target)
        if not entry or not entry.get("snapshots"):
            return None, entry
        return entry["snapshots"][-1]["data"], entry
