"""Data layer for Projectum.

Each root folder gets a single ``.projectum.json`` file holding per-subfolder
state (completion, notes, tags) plus an ``_orphans`` bucket that preserves
data for folders that have disappeared, so a later ``git checkout`` or
rename brings the metadata back.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Project:
    name: str
    path: str
    completed: bool = False
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    position: int = 0  # manual sort key; pinned items still float to top
    tested: bool = False

    @property
    def folder(self) -> Path:
        return Path(self.path)

    def last_modified(self) -> datetime | None:
        try:
            return datetime.fromtimestamp(self.folder.stat().st_mtime)
        except OSError:
            return None

    def exists(self) -> bool:
        return self.folder.is_dir()


@dataclass
class Video:
    id: str
    title: str
    url: str
    duration: int | None = None       # seconds, may be missing
    completed: bool = False
    notes: str = ""
    unavailable: bool = False          # True if the video was removed upstream


@dataclass
class Playlist:
    id: str
    url: str
    title: str
    uploader: str = ""
    fetched_at: str = ""               # ISO timestamp
    videos: list[Video] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    notes: str = ""                    # free-form per-playlist notes
    pinned: bool = False
    position: int = 0                  # manual sort key

    @property
    def total(self) -> int:
        return len(self.videos)

    @property
    def watched(self) -> int:
        return sum(1 for v in self.videos if v.completed)

    @property
    def percent(self) -> int:
        return round(self.watched * 100 / self.total) if self.total else 0

    def merge_fetch(self, fetched: dict) -> None:
        """Update title/uploader from a fresh fetch; reconcile videos by ID.

        Existing notes/completion are preserved. Videos no longer present
        upstream are kept but flagged ``unavailable``.
        """
        self.title = fetched.get("title", self.title)
        self.uploader = fetched.get("uploader", self.uploader)
        self.fetched_at = datetime.now().isoformat(timespec="seconds")

        new_videos = fetched.get("videos", []) or []
        existing = {v.id: v for v in self.videos}
        seen: set[str] = set()
        merged: list[Video] = []

        for v in new_videos:
            vid = v.get("id") or ""
            if not vid:
                continue
            seen.add(vid)
            if vid in existing:
                e = existing[vid]
                e.title = v.get("title", e.title)
                e.duration = v.get("duration", e.duration)
                e.url = v.get("url", e.url)
                e.unavailable = False
                merged.append(e)
            else:
                merged.append(
                    Video(
                        id=vid,
                        title=v.get("title") or "(no title)",
                        url=v.get("url") or f"https://www.youtube.com/watch?v={vid}",
                        duration=v.get("duration"),
                    )
                )

        for vid, v in existing.items():
            if vid not in seen:
                v.unavailable = True
                merged.append(v)

        self.videos = merged


class ProjectStore:
    STORE_FILENAME = ".projectum.json"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.projects: dict[str, Project] = {}
        self.orphans: dict[str, dict] = {}
        self.tag_colors: dict[str, str] = {}
        self.playlists: list[Playlist] = []
        self.notes: str = ""  # folder-level scratchpad shown in the Notes tab
        self.load()

    @property
    def store_path(self) -> Path:
        return self.root / self.STORE_FILENAME

    def load(self) -> None:
        data: dict = {}
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}

        persisted_active = data.get("projects", {}) or {}
        persisted_orphans = data.get("_orphans", {}) or {}
        all_persisted = {**persisted_orphans, **persisted_active}
        self.tag_colors = dict(data.get("tag_colors", {}) or {})
        self.notes = str(data.get("notes", ""))

        try:
            entries = sorted(
                (p for p in self.root.iterdir()
                 if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            entries = []

        # Preserve existing Project instances by name when reloading. The
        # alternative — recreating instances every load — leaves callers
        # holding stale references (current_project, sidebar rows), so
        # toggles on those orphans don't persist when save() iterates
        # self.projects. Same logic for playlists (by id) and their videos
        # (by id) below.
        existing_projects = self.projects
        new_projects: dict[str, Project] = {}
        seen: set[str] = set()
        for entry in entries:
            name = entry.name
            seen.add(name)
            d = all_persisted.get(name, {})
            ex = existing_projects.get(name)
            if ex is not None:
                ex.path = str(entry)
                ex.completed = bool(d.get("completed", False))
                ex.notes = str(d.get("notes", ""))
                ex.tags = list(d.get("tags", []) or [])
                ex.pinned = bool(d.get("pinned", False))
                ex.position = int(d.get("position", 0))
                ex.tested = bool(d.get("tested", False))
                new_projects[name] = ex
            else:
                new_projects[name] = Project(
                    name=name,
                    path=str(entry),
                    completed=bool(d.get("completed", False)),
                    notes=str(d.get("notes", "")),
                    tags=list(d.get("tags", []) or []),
                    pinned=bool(d.get("pinned", False)),
                    position=int(d.get("position", 0)),
                    tested=bool(d.get("tested", False)),
                )
        self.projects = new_projects

        self.orphans = {
            name: d
            for name, d in all_persisted.items()
            if name not in seen
            and (d.get("notes") or d.get("tags") or d.get("completed"))
        }

        existing_playlists = {pl.id: pl for pl in self.playlists}
        new_playlists: list[Playlist] = []
        for pdata in data.get("playlists", []) or []:
            pid = pdata.get("id") or uuid.uuid4().hex
            existing_pl = existing_playlists.get(pid)
            existing_videos = (
                {v.id: v for v in existing_pl.videos} if existing_pl else {}
            )
            videos: list[Video] = []
            for v in (pdata.get("videos") or []):
                vid = v.get("id") or ""
                if not vid:
                    continue
                ev = existing_videos.get(vid)
                if ev is not None:
                    ev.title = v.get("title", "(no title)")
                    ev.url = v.get("url", "")
                    ev.duration = v.get("duration")
                    ev.completed = bool(v.get("completed", False))
                    ev.notes = str(v.get("notes", ""))
                    ev.unavailable = bool(v.get("unavailable", False))
                    videos.append(ev)
                else:
                    videos.append(Video(
                        id=vid,
                        title=v.get("title", "(no title)"),
                        url=v.get("url", ""),
                        duration=v.get("duration"),
                        completed=bool(v.get("completed", False)),
                        notes=str(v.get("notes", "")),
                        unavailable=bool(v.get("unavailable", False)),
                    ))
            if existing_pl is not None:
                existing_pl.url = pdata.get("url", "")
                existing_pl.title = pdata.get("title", "(untitled)")
                existing_pl.uploader = pdata.get("uploader", "")
                existing_pl.fetched_at = pdata.get("fetched_at", "")
                existing_pl.tags = list(pdata.get("tags", []) or [])
                existing_pl.notes = str(pdata.get("notes", ""))
                existing_pl.pinned = bool(pdata.get("pinned", False))
                existing_pl.position = int(pdata.get("position", 0))
                existing_pl.videos = videos
                new_playlists.append(existing_pl)
            else:
                new_playlists.append(Playlist(
                    id=pid,
                    url=pdata.get("url", ""),
                    title=pdata.get("title", "(untitled)"),
                    uploader=pdata.get("uploader", ""),
                    fetched_at=pdata.get("fetched_at", ""),
                    videos=videos,
                    tags=list(pdata.get("tags", []) or []),
                    notes=str(pdata.get("notes", "")),
                    pinned=bool(pdata.get("pinned", False)),
                    position=int(pdata.get("position", 0)),
                ))
        self.playlists = new_playlists

    def save(self) -> None:
        payload = {
            "version": 1,
            "notes": self.notes,
            "projects": {
                name: {
                    "completed": p.completed,
                    "notes": p.notes,
                    "tags": p.tags,
                    "pinned": p.pinned,
                    "position": p.position,
                    "tested": p.tested,
                }
                for name, p in self.projects.items()
            },
            "_orphans": self.orphans,
            "tag_colors": self.tag_colors,
            "playlists": [
                {
                    "id": pl.id,
                    "url": pl.url,
                    "title": pl.title,
                    "uploader": pl.uploader,
                    "fetched_at": pl.fetched_at,
                    "tags": pl.tags,
                    "notes": pl.notes,
                    "pinned": pl.pinned,
                    "position": pl.position,
                    "videos": [
                        {
                            "id": v.id,
                            "title": v.title,
                            "url": v.url,
                            "duration": v.duration,
                            "completed": v.completed,
                            "notes": v.notes,
                            "unavailable": v.unavailable,
                        }
                        for v in pl.videos
                    ],
                }
                for pl in self.playlists
            ],
        }
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.store_path)

    def sorted_projects(self) -> list[Project]:
        """Pinned first (by position, then name), then unpinned (same)."""
        return sorted(
            self.projects.values(),
            key=lambda p: (not p.pinned, p.position, p.name.casefold()),
        )

    def sorted_playlists(self) -> list[Playlist]:
        return sorted(
            self.playlists,
            key=lambda pl: (not pl.pinned, pl.position, pl.title.casefold()),
        )

    def reorder_projects(self, names_in_order: list[str]) -> None:
        """Assign sequential ``position`` values to projects matching ``names_in_order``."""
        for i, name in enumerate(names_in_order):
            p = self.projects.get(name)
            if p is not None:
                p.position = i

    def reorder_playlists(self, ids_in_order: list[str]) -> None:
        by_id = {pl.id: pl for pl in self.playlists}
        for i, pid in enumerate(ids_in_order):
            pl = by_id.get(pid)
            if pl is not None:
                pl.position = i

    def all_tags(self) -> list[str]:
        seen: set[str] = set()
        for p in self.projects.values():
            seen.update(p.tags)
        for pl in self.playlists:
            seen.update(pl.tags)
        return sorted(seen)

    def prune_unused_tag_colors(self) -> list[str]:
        """Drop ``tag_colors`` entries for tags no project or playlist uses.

        Orphan project data (folders that have disappeared) is included in
        the "in use" set so a later ``git checkout`` restores tag colors
        along with the rest of the project state.

        Returns the removed tag names. Caller is responsible for ``save()``.
        """
        in_use = set(self.all_tags())
        for orphan_data in self.orphans.values():
            in_use.update(orphan_data.get("tags") or [])
        removed = [t for t in self.tag_colors if t not in in_use]
        for t in removed:
            del self.tag_colors[t]
        return removed

    def stats(self) -> tuple[int, int]:
        total = len(self.projects)
        done = sum(1 for p in self.projects.values() if p.completed)
        return done, total

    # ── Playlists ──

    def add_playlist(self, url: str, fetched: dict) -> Playlist:
        pl = Playlist(
            id=uuid.uuid4().hex,
            url=url,
            title=fetched.get("title", "(untitled)"),
            uploader=fetched.get("uploader", ""),
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            videos=[
                Video(
                    id=v.get("id") or "",
                    title=v.get("title") or "(no title)",
                    url=v.get("url") or (
                        f"https://www.youtube.com/watch?v={v.get('id')}"
                        if v.get("id") else ""
                    ),
                    duration=v.get("duration"),
                )
                for v in (fetched.get("videos") or [])
                if v.get("id")
            ],
        )
        self.playlists.append(pl)
        self.save()
        return pl

    def remove_playlist(self, playlist_id: str) -> bool:
        for i, pl in enumerate(self.playlists):
            if pl.id == playlist_id:
                del self.playlists[i]
                self.save()
                return True
        return False

    def get_playlist(self, playlist_id: str) -> Playlist | None:
        for pl in self.playlists:
            if pl.id == playlist_id:
                return pl
        return None
