"""Background check for a newer GitHub release.

A single read-only GET to the public GitHub Releases API — no auth, no
telemetry, no identifying data. Runs on the thread pool and fails silently
(offline, rate-limited, parse error → no banner). The version comparison is a
pure function so it's easy to test.
"""

from __future__ import annotations

import json
import re
import urllib.request

from PySide6.QtCore import QObject, QRunnable, Signal

GITHUB_REPO = "wleeaf/projectum"
_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.7.0"`` → ``(1, 7, 0)``. Stops at the first non-numeric segment
    (so pre-release suffixes are ignored), returns ``()`` if unparseable."""
    parts: list[int] = []
    for seg in (text or "").strip().lstrip("vV").split("."):
        m = re.match(r"\d+", seg)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer version than ``current``."""
    lv = parse_version(latest)
    return bool(lv) and lv > parse_version(current)


class UpdateCheckSignals(QObject):
    # Emitted only when a newer release exists: (tag, release page URL).
    update_available = Signal(str, str)


class UpdateCheckRunnable(QRunnable):
    """Fetches the latest release tag off the UI thread."""

    def __init__(self, current_version: str):
        super().__init__()
        self.current_version = current_version
        self.signals = UpdateCheckSignals()

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                _LATEST_URL,
                headers={
                    "User-Agent": "Projectum-update-check",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return  # fail-silent: offline / rate-limited / bad response
        if not isinstance(data, dict):
            return
        tag = str(data.get("tag_name") or "")
        url = str(data.get("html_url") or _RELEASES_PAGE)
        if tag and is_newer(tag, self.current_version):
            self.signals.update_available.emit(tag, url)
