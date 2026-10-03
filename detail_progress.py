"""Remember detail coverage, never cached scores or application confirmations.

Each workflow has its own queue so previewing jobs cannot displace live work.
Completed reports advance the queue; every selected job still needs a fresh
description before it may be applied to.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import config


def matching_policy_fingerprint() -> str:
    """Restart coverage when the user's matching policy changes."""
    names = (
        "TARGET_TITLES", "REQUIRED_SKILLS", "PREFERRED_SKILLS", "BLOCKED_KEYWORDS",
        "ROLE_SIGNALS", "MAX_REQUIRED_EXPERIENCE", "MINIMUM_SCORE", "TITLE_WEIGHT",
        "REQUIRED_SKILLS_WEIGHT", "PREFERRED_SKILLS_WEIGHT", "EXPERIENCE_WEIGHT",
    )
    policy = {name: getattr(config, name) for name in names}
    policy = {name: sorted(value) if isinstance(value, (set, frozenset)) else value
              for name, value in policy.items()}
    policy["version"] = 1
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


class DetailProgress:
    """A bounded, optional coverage journal, isolated from application history."""

    def __init__(self, path: Path, mode: str, *, now: datetime | None = None):
        if mode not in {"live", "dry_run", "audit"}:
            raise ValueError("Unknown detail-progress mode")
        self.path = path
        self.mode = mode
        self.policy = matching_policy_fingerprint()
        self.checked_at: dict[str, float] = {}
        current = now or datetime.now(timezone.utc)
        earliest = (current - timedelta(days=7)).timestamp()
        document = self._read_document()
        scope = document.get("modes", {}).get(mode, {})
        if not isinstance(scope, dict) or scope.get("policy") != self.policy:
            return
        entries = scope.get("checked_at", {})
        if not isinstance(entries, dict):
            return
        for url, timestamp in entries.items():
            try:
                parsed = datetime.fromisoformat(timestamp)
                if parsed.tzinfo is not None and earliest <= parsed.timestamp() <= current.timestamp():
                    self.checked_at[url] = parsed.timestamp()
            except (TypeError, ValueError, OverflowError):
                continue

    def _read_document(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("modes"), dict):
                return data
        except (OSError, ValueError):
            pass
        # This journal is only scheduling data. Bad data causes a fresh review;
        # it must never suppress a job or alter successful-application history.
        return {"version": 1, "modes": {}}

    def mark_checked(self, urls: Iterable[str], *, now: datetime | None = None) -> None:
        """Advance only after the corresponding outcome report was saved."""
        urls = list(urls)
        if not urls:
            return
        current = now or datetime.now(timezone.utc)
        for url in urls:
            self.checked_at[url] = current.timestamp()
        document = self._read_document()
        document["modes"][self.mode] = {
            "policy": self.policy,
            "checked_at": {
                url: datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                for url, timestamp in self.checked_at.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)
