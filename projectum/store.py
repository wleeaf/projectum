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
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _as_int(value, default: int = 0) -> int:
    """Coerce a persisted value to int, falling back on bad/missing data."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value, default: str = "") -> str:
    """Return ``value`` if it is a string, else ``default``."""
    return value if isinstance(value, str) else default


def _as_str_list(value) -> list[str]:
    """Coerce a persisted value to a list of strings.

    A bare string (e.g. a hand-edited ``"tags": "abc"``) must NOT be exploded
    into individual characters, so anything that isn't already a list is
    treated as empty.
    """
    if not isinstance(value, list):
        return []
    return [str(x) for x in value]


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
    suspended: bool = False  # on hold / paused
    failed: bool = False     # abandoned / didn't pan out
    # Calendar scheduling. ISO "YYYY-MM-DD"; "" == unscheduled. `end` is
    # INCLUSIVE — start == end (or end == "") means a single day. This
    # convention is shared by storage, the layout helper, and the day-bucket
    # query in calendar.py; keep them in lockstep.
    start: str = ""
    end: str = ""

    @property
    def folder(self) -> Path:
        return Path(self.path)

    @property
    def depth(self) -> int:
        """Nesting level below the root. 0 for a direct subfolder; 1+ for the
        descendants surfaced by an expanded folder (``name`` is the POSIX path
        relative to the root, so depth is its separator count)."""
        return self.name.count("/")

    @property
    def leaf(self) -> str:
        """The folder's own name (last path segment) for display."""
        return self.name.rsplit("/", 1)[-1]

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
    start: str = ""                    # calendar: ISO date, "" == unscheduled
    end: str = ""                      # inclusive end; "" / == start => single day

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
        # Bucket existing videos by id in FIFO order so a playlist that
        # legitimately repeats a video id pairs each fetched occurrence with a
        # distinct existing Video (preserving its completion/notes) instead of
        # collapsing every occurrence onto one shared object.
        existing: dict[str, deque[Video]] = {}
        for v in self.videos:
            existing.setdefault(v.id, deque()).append(v)
        merged: list[Video] = []

        for v in new_videos:
            vid = v.get("id") or ""
            if not vid:
                continue
            bucket = existing.get(vid)
            if bucket:
                e = bucket.popleft()
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

        # Anything left in the buckets is no longer upstream (or a surplus
        # duplicate the fresh fetch didn't account for) — keep it, flagged
        # unavailable.
        for bucket in existing.values():
            for v in bucket:
                v.unavailable = True
                merged.append(v)

        self.videos = merged


@dataclass
class Todo:
    id: str
    text: str
    done: bool = False
    position: int = 0  # manual sort key (drag-to-reorder)
    start: str = ""    # calendar: ISO date, "" == unscheduled
    end: str = ""      # inclusive end; "" / == start => single day


@dataclass
class Note:
    """One free-standing note in the folder-wide Notes tab.

    The Notes tab is a small notebook of these rather than a single scratchpad.
    ``body`` is raw Markdown rendered live in the editor.
    """
    id: str
    title: str = ""
    body: str = ""
    position: int = 0  # manual sort key (drag-to-reorder)


def _note_title_from_body(body: str) -> str:
    """Derive a title from a note's first non-empty line.

    Used when migrating the legacy single scratchpad and when a note is saved
    without an explicit title. Strips a leading Markdown heading marker.
    """
    for line in body.splitlines():
        line = line.strip()
        if line:
            line = line.lstrip("#").strip() or line
            return line[:80]
    return ""


class ProjectStore:
    STORE_FILENAME = ".projectum.json"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.projects: dict[str, Project] = {}
        self.orphans: dict[str, dict] = {}
        self.tag_colors: dict[str, str] = {}
        # Folders the user chose to expand: POSIX relpath -> how many levels of
        # subfolders below it also become projects. Empty == classic flat scan.
        self.expansions: dict[str, int] = {}
        self.playlists: list[Playlist] = []
        self.todos: list[Todo] = []  # folder-level to-do list (Todo tab)
        self.notes: str = ""  # legacy single scratchpad; migrated into note_docs
        self.note_docs: list[Note] = []  # folder-level notes shown in Notes tab
        self.load()

    @property
    def store_path(self) -> Path:
        return self.root / self.STORE_FILENAME

    SCAN_BUDGET = 2000  # cap on dirs surfaced by expansions (keep the scan cheap)

    def _scan_project_dirs(self) -> dict[str, Path]:
        """Map POSIX relpath -> absolute Path for every project folder: the
        root's direct subfolders, plus the depth-bounded subtrees of any
        expanded folders. Skips dotfiles and symlinked dirs (loop-safe) and is
        capped at ``SCAN_BUDGET`` so one huge expansion can't stall the scan."""
        results: dict[str, Path] = {}
        try:
            for entry in self.root.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    results[entry.name] = entry
        except OSError:
            pass
        budget = self.SCAN_BUDGET
        for relpath, depth in self.expansions.items():
            base = self.root / relpath
            if depth <= 0 or not base.is_dir():
                continue
            stack = [(base, relpath, 0)]
            while stack and budget > 0:
                cur, cur_rel, lvl = stack.pop()
                if lvl >= depth:
                    continue
                try:
                    children = list(cur.iterdir())
                except OSError:
                    continue
                for child in children:
                    if budget <= 0:
                        break
                    if (child.name.startswith(".") or child.is_symlink()
                            or not child.is_dir()):
                        continue
                    rel = f"{cur_rel}/{child.name}"
                    if rel not in results:
                        results[rel] = child
                        budget -= 1
                    stack.append((child, rel, lvl + 1))
        return results

    def set_expansion(self, relpath: str, depth: int) -> None:
        """Show ``depth`` levels of ``relpath``'s subfolders as projects too
        (``depth`` <= 0 stops expanding it). Rescans so it takes effect."""
        if depth and depth > 0:
            self.expansions[relpath] = int(depth)
        else:
            self.expansions.pop(relpath, None)
        self.save()
        self.load()

    def load(self) -> None:
        data: dict = {}
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        # A structurally-valid-but-wrong-typed file (e.g. a top-level list)
        # must not abort the whole load.
        if not isinstance(data, dict):
            data = {}

        persisted_active = data.get("projects") or {}
        persisted_orphans = data.get("_orphans") or {}
        if not isinstance(persisted_active, dict):
            persisted_active = {}
        if not isinstance(persisted_orphans, dict):
            persisted_orphans = {}
        all_persisted = {**persisted_orphans, **persisted_active}
        tag_colors = data.get("tag_colors") or {}
        self.tag_colors = dict(tag_colors) if isinstance(tag_colors, dict) else {}
        self.notes = str(data.get("notes", ""))

        raw_exp = data.get("expansions")
        self.expansions = {}
        if isinstance(raw_exp, dict):
            for k, v in raw_exp.items():
                depth = _as_int(v)
                if isinstance(k, str) and depth > 0:
                    self.expansions[k] = depth

        scanned = self._scan_project_dirs()   # {posix relpath: absolute Path}

        # Preserve existing Project instances by name when reloading. The
        # alternative — recreating instances every load — leaves callers
        # holding stale references (current_project, sidebar rows), so
        # toggles on those orphans don't persist when save() iterates
        # self.projects. Same logic for playlists (by id) and their videos
        # (by id) below.
        existing_projects = self.projects
        new_projects: dict[str, Project] = {}
        seen: set[str] = set()
        for name in sorted(scanned, key=str.lower):
            entry = scanned[name]      # name is the POSIX relpath; entry the abspath
            seen.add(name)
            d = all_persisted.get(name, {})
            if not isinstance(d, dict):
                d = {}
            ex = existing_projects.get(name)
            if ex is not None:
                ex.path = str(entry)
                ex.completed = bool(d.get("completed", False))
                ex.notes = str(d.get("notes", ""))
                ex.tags = _as_str_list(d.get("tags"))
                ex.pinned = bool(d.get("pinned", False))
                ex.position = _as_int(d.get("position", 0))
                ex.tested = bool(d.get("tested", False))
                ex.suspended = bool(d.get("suspended", False))
                ex.failed = bool(d.get("failed", False))
                ex.start = _as_str(d.get("start"))
                ex.end = _as_str(d.get("end"))
                new_projects[name] = ex
            else:
                new_projects[name] = Project(
                    name=name,
                    path=str(entry),
                    completed=bool(d.get("completed", False)),
                    notes=str(d.get("notes", "")),
                    tags=_as_str_list(d.get("tags")),
                    pinned=bool(d.get("pinned", False)),
                    position=_as_int(d.get("position", 0)),
                    tested=bool(d.get("tested", False)),
                    suspended=bool(d.get("suspended", False)),
                    failed=bool(d.get("failed", False)),
                    start=_as_str(d.get("start")),
                    end=_as_str(d.get("end")),
                )
        self.projects = new_projects

        self.orphans = {
            name: d
            for name, d in all_persisted.items()
            if name not in seen
            and isinstance(d, dict)
            and (
                d.get("notes") or d.get("tags")
                or d.get("completed") or d.get("tested")
                or d.get("suspended") or d.get("failed")
                or d.get("pinned") or d.get("position")
                or d.get("start") or d.get("end")
            )
        }

        existing_playlists = {pl.id: pl for pl in self.playlists}
        new_playlists: list[Playlist] = []
        raw_playlists = data.get("playlists") or []
        if not isinstance(raw_playlists, list):
            raw_playlists = []
        for pdata in raw_playlists:
            if not isinstance(pdata, dict):
                continue
            pid = pdata.get("id") or uuid.uuid4().hex
            existing_pl = existing_playlists.get(pid)
            # Bucket existing video instances by id (FIFO) so repeated ids
            # don't collapse onto one shared object on reload.
            existing_videos: dict[str, deque[Video]] = {}
            if existing_pl:
                for ev in existing_pl.videos:
                    existing_videos.setdefault(ev.id, deque()).append(ev)
            videos: list[Video] = []
            for v in (pdata.get("videos") or []):
                if not isinstance(v, dict):
                    continue
                vid = v.get("id") or ""
                if not vid:
                    continue
                bucket = existing_videos.get(vid)
                ev = bucket.popleft() if bucket else None
                if ev is not None:
                    ev.title = _as_str(v.get("title"), "(no title)")
                    ev.url = _as_str(v.get("url"), "")
                    ev.duration = v.get("duration")
                    ev.completed = bool(v.get("completed", False))
                    ev.notes = str(v.get("notes", ""))
                    ev.unavailable = bool(v.get("unavailable", False))
                    videos.append(ev)
                else:
                    videos.append(Video(
                        id=vid,
                        title=_as_str(v.get("title"), "(no title)"),
                        url=_as_str(v.get("url"), ""),
                        duration=v.get("duration"),
                        completed=bool(v.get("completed", False)),
                        notes=str(v.get("notes", "")),
                        unavailable=bool(v.get("unavailable", False)),
                    ))
            if existing_pl is not None:
                existing_pl.url = _as_str(pdata.get("url"), "")
                existing_pl.title = _as_str(pdata.get("title"), "(untitled)")
                existing_pl.uploader = _as_str(pdata.get("uploader"), "")
                existing_pl.fetched_at = _as_str(pdata.get("fetched_at"), "")
                existing_pl.tags = _as_str_list(pdata.get("tags"))
                existing_pl.notes = str(pdata.get("notes", ""))
                existing_pl.pinned = bool(pdata.get("pinned", False))
                existing_pl.position = _as_int(pdata.get("position", 0))
                existing_pl.start = _as_str(pdata.get("start"))
                existing_pl.end = _as_str(pdata.get("end"))
                existing_pl.videos = videos
                new_playlists.append(existing_pl)
            else:
                new_playlists.append(Playlist(
                    id=pid,
                    url=_as_str(pdata.get("url"), ""),
                    title=_as_str(pdata.get("title"), "(untitled)"),
                    uploader=_as_str(pdata.get("uploader"), ""),
                    fetched_at=_as_str(pdata.get("fetched_at"), ""),
                    videos=videos,
                    tags=_as_str_list(pdata.get("tags")),
                    notes=str(pdata.get("notes", "")),
                    pinned=bool(pdata.get("pinned", False)),
                    position=_as_int(pdata.get("position", 0)),
                    start=_as_str(pdata.get("start")),
                    end=_as_str(pdata.get("end")),
                ))
        self.playlists = new_playlists

        # ── Todos ── preserve existing instances by id (same rationale as
        # projects/playlists: callers/rows hold references across reloads).
        existing_todos = {t.id: t for t in self.todos}
        new_todos: list[Todo] = []
        raw_todos = data.get("todos") or []
        if not isinstance(raw_todos, list):
            raw_todos = []
        for tdata in raw_todos:
            if not isinstance(tdata, dict):
                continue
            tid = tdata.get("id") or uuid.uuid4().hex
            et = existing_todos.get(tid)
            text = _as_str(tdata.get("text"), "")
            done = bool(tdata.get("done", False))
            position = _as_int(tdata.get("position", 0))
            start = _as_str(tdata.get("start"))
            end = _as_str(tdata.get("end"))
            if et is not None:
                et.text, et.done, et.position = text, done, position
                et.start, et.end = start, end
                new_todos.append(et)
            else:
                new_todos.append(Todo(id=tid, text=text, done=done, position=position,
                                      start=start, end=end))
        self.todos = new_todos

        # ── Notes ── preserve existing instances by id. When the modern
        # ``note_docs`` key is absent, migrate the legacy single scratchpad
        # (``notes``) into one note — one-way, only when there's something to
        # carry over. A present-but-empty list means the user cleared them.
        existing_notes = {n.id: n for n in self.note_docs}
        new_notes: list[Note] = []
        raw_note_docs = data.get("note_docs")
        if isinstance(raw_note_docs, list):
            for ndata in raw_note_docs:
                if not isinstance(ndata, dict):
                    continue
                nid = ndata.get("id") or uuid.uuid4().hex
                title = _as_str(ndata.get("title"))
                body = str(ndata.get("body", ""))
                position = _as_int(ndata.get("position", 0))
                en = existing_notes.get(nid)
                if en is not None:
                    en.title, en.body, en.position = title, body, position
                    new_notes.append(en)
                else:
                    new_notes.append(Note(id=nid, title=title, body=body,
                                          position=position))
        elif self.notes.strip():
            new_notes.append(Note(
                id=uuid.uuid4().hex,
                title=_note_title_from_body(self.notes),
                body=self.notes,
                position=0,
            ))
        self.note_docs = new_notes

    def save(self) -> None:
        payload = {
            "version": 2,
            "note_docs": [
                {"id": n.id, "title": n.title, "body": n.body,
                 "position": n.position}
                for n in self.note_docs
            ],
            "projects": {
                name: {
                    "completed": p.completed,
                    "notes": p.notes,
                    "tags": p.tags,
                    "pinned": p.pinned,
                    "position": p.position,
                    "tested": p.tested,
                    "suspended": p.suspended,
                    "failed": p.failed,
                    "start": p.start,
                    "end": p.end,
                }
                for name, p in self.projects.items()
            },
            "_orphans": self.orphans,
            "tag_colors": self.tag_colors,
            "expansions": self.expansions,
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
                    "start": pl.start,
                    "end": pl.end,
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
            "todos": [
                {
                    "id": t.id,
                    "text": t.text,
                    "done": t.done,
                    "position": t.position,
                    "start": t.start,
                    "end": t.end,
                }
                for t in self.todos
            ],
        }
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.store_path)

    def sorted_projects(self) -> list[Project]:
        """Tree order: each folder immediately precedes its expanded subtree,
        with siblings at every level ordered pinned-first, then by position, then
        name. With no expansions this collapses to the classic flat ordering."""
        by_name = self.projects

        def key(p: Project):
            parts = p.name.split("/")
            out = []
            for i in range(len(parts)):
                anc = by_name.get("/".join(parts[:i + 1]))
                if anc is not None:
                    out.append((not anc.pinned, anc.position, parts[i].casefold()))
                else:
                    out.append((True, 0, parts[i].casefold()))
            return out

        return sorted(by_name.values(), key=key)

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

    # ── Todos ──

    def sorted_todos(self) -> list[Todo]:
        """Todos in manual order (``position``); ties keep insertion order."""
        return sorted(self.todos, key=lambda t: t.position)

    def add_todo(self, text: str) -> Todo:
        pos = max((t.position for t in self.todos), default=-1) + 1
        todo = Todo(id=uuid.uuid4().hex, text=text, position=pos)
        self.todos.append(todo)
        self.save()
        return todo

    def remove_todo(self, todo_id: str) -> bool:
        for i, t in enumerate(self.todos):
            if t.id == todo_id:
                del self.todos[i]
                self.save()
                return True
        return False

    def get_todo(self, todo_id: str) -> Todo | None:
        for t in self.todos:
            if t.id == todo_id:
                return t
        return None

    def reorder_todos(self, ids_in_order: list[str]) -> None:
        """Assign sequential ``position`` values. Caller saves."""
        by_id = {t.id: t for t in self.todos}
        for i, tid in enumerate(ids_in_order):
            t = by_id.get(tid)
            if t is not None:
                t.position = i

    def todo_stats(self) -> tuple[int, int]:
        done = sum(1 for t in self.todos if t.done)
        return done, len(self.todos)

    # ── Notes (folder-wide Notes tab) ──

    def sorted_notes(self) -> list[Note]:
        """Notes in manual order (``position``); ties keep insertion order."""
        return sorted(self.note_docs, key=lambda n: n.position)

    def add_note(self, title: str = "", body: str = "") -> Note:
        pos = max((n.position for n in self.note_docs), default=-1) + 1
        note = Note(id=uuid.uuid4().hex, title=title, body=body, position=pos)
        self.note_docs.append(note)
        self.save()
        return note

    def remove_note(self, note_id: str) -> bool:
        for i, n in enumerate(self.note_docs):
            if n.id == note_id:
                del self.note_docs[i]
                self.save()
                return True
        return False

    def get_note(self, note_id: str) -> Note | None:
        for n in self.note_docs:
            if n.id == note_id:
                return n
        return None

    def reorder_notes(self, ids_in_order: list[str]) -> None:
        """Assign sequential ``position`` values. Caller saves."""
        by_id = {n.id: n for n in self.note_docs}
        for i, nid in enumerate(ids_in_order):
            n = by_id.get(nid)
            if n is not None:
                n.position = i
