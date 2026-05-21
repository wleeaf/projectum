"""YouTube playlist fetcher using yt-dlp.

The runnable is meant to run on Qt's thread pool. ``yt_dlp`` is imported
lazily inside :meth:`run` so a missing or broken install can't crash
application startup — the caller surfaces the ImportError as a fetch
failure instead.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal


class PlaylistFetchSignals(QObject):
    # Both signals carry the URL so slot handlers don't need to capture it.
    done = Signal(str, dict)     # url, {"title": str, "uploader": str, "videos": [...]}
    failed = Signal(str, str)    # url, human-readable error


class PlaylistFetchRunnable(QRunnable):
    """Fetches playlist metadata + flat video list off the UI thread."""

    def __init__(self, url: str):
        super().__init__()
        self.url = url.strip()
        self.signals = PlaylistFetchSignals()

    def run(self) -> None:
        if not self.url:
            self.signals.failed.emit(self.url,"No URL provided.")
            return

        try:
            import yt_dlp  # lazy: don't break app launch on bad install
        except Exception as e:
            self.signals.failed.emit(self.url,f"yt-dlp unavailable: {e}")
            return

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "socket_timeout": 10,
            "noplaylist": False,
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
        except Exception as e:
            self.signals.failed.emit(self.url,self._friendly_error(e))
            return

        if not info:
            self.signals.failed.emit(self.url,"No playlist data returned.")
            return

        entries = info.get("entries") or []
        if not entries:
            self.signals.failed.emit(self.url,
                "That URL didn't resolve to a playlist with videos."
            )
            return

        videos = []
        for e in entries:
            if not e or not e.get("id"):
                continue
            vid = e["id"]
            videos.append({
                "id": vid,
                "title": e.get("title") or "(no title)",
                "duration": e.get("duration"),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
            })

        self.signals.done.emit(self.url, {
            "title": info.get("title") or "Untitled playlist",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "videos": videos,
        })

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        msg = str(exc)
        low = msg.lower()
        if "private" in low:
            return "That playlist is private."
        if "does not exist" in low or "not found" in low:
            return "Playlist not found."
        if "timed out" in low or "timeout" in low:
            return "Fetch timed out. Check your connection and try again."
        if "unsupported url" in low:
            return "That doesn't look like a YouTube playlist URL."
        return msg.splitlines()[0] if msg else "Unknown error fetching playlist."
