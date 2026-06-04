"""Cross-folder scheduling for the Calendar tab.

The calendar is *global*: it aggregates scheduled projects, playlists, and
todos from every folder the user tracks (the persisted ``calendar_folders``
set, seeded from recent folders), not just the open one.

Two things here are deliberately careful:

* **Reads reuse :class:`~projectum.store.ProjectStore`.** Its ``load()`` is
  read-only (no disk writes, one-level ``iterdir``) and already defends against
  hand-edited / corrupt files. Rather than maintain a second parser that could
  drift from it, we construct a throwaway store per folder — each wrapped so one
  bad folder can't break the scan for the rest.

* **Writes route by resolved-path identity.** Editing a date for the *open*
  folder must go through the live in-memory store (whose next ``save()`` would
  otherwise clobber a write made through a second instance). The discriminator
  is ``Path.resolve()`` equality — never string equality, since the tracked
  paths can differ by trailing slash, symlink, or case.

Item identity is the composite ``(home, kind, key)``: project keys (folder
names like ``src``/``tests``) recur across folders, so the home path is part of
identity everywhere — drag, move, resize, and write-back.

Dates are ISO ``YYYY-MM-DD``; ``""`` means unscheduled. ``end`` is **inclusive**
(``start == end`` is a single day), a convention shared with ``store.py`` and
the layout helper below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .store import ProjectStore, _note_title_from_body

# Week starts on Monday (ISO). date.weekday(): Monday == 0 … Sunday == 6.
WEEK_START = 0

KIND_PROJECT = "project"
KIND_PLAYLIST = "playlist"
KIND_TODO = "todo"
KIND_NOTE = "note"


def parse_date(text: str) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` string, returning None on empty/garbage."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class ScheduledItem:
    """One schedulable thing, located by the composite ``(home, kind, key)``.

    ``home`` is the tracked folder's root path (its ``.projectum.json`` lives
    inside). ``key`` is the folder name for projects and the UUID for
    playlists/todos. ``start``/``end`` are inclusive ISO dates; both ``""``
    means unscheduled.
    """

    home: str
    kind: str
    key: str
    title: str
    start: str = ""
    end: str = ""
    done: bool = False
    orphan: bool = False  # project whose folder has disappeared

    @property
    def scheduled(self) -> bool:
        return bool(self.start)

    @property
    def home_name(self) -> str:
        return Path(self.home).name or self.home

    def identity(self) -> tuple[str, str, str]:
        """Stable identity that survives a rescan (resolved home + kind + key)."""
        return (_resolved(self.home), self.kind, self.key)


# ── building items from a store ──────────────────────────────────────────────

def items_from_store(store: ProjectStore) -> list[ScheduledItem]:
    """All schedulable items in one store (projects incl. orphans, playlists, todos)."""
    home = str(store.root)
    items: list[ScheduledItem] = []

    for name, p in store.projects.items():
        items.append(ScheduledItem(home, KIND_PROJECT, name, name,
                                   p.start, p.end, p.completed))
    # Orphaned projects (folder vanished) may still carry a schedule — show them.
    for name, d in store.orphans.items():
        if not isinstance(d, dict):
            continue
        items.append(ScheduledItem(
            home, KIND_PROJECT, name, name,
            d.get("start") if isinstance(d.get("start"), str) else "",
            d.get("end") if isinstance(d.get("end"), str) else "",
            bool(d.get("completed")), orphan=True,
        ))
    for pl in store.playlists:
        done = pl.total > 0 and pl.watched == pl.total
        items.append(ScheduledItem(home, KIND_PLAYLIST, pl.id,
                                   pl.title or "(untitled)", pl.start, pl.end, done))
    for t in store.todos:
        items.append(ScheduledItem(home, KIND_TODO, t.id, t.text,
                                   t.start, t.end, t.done))
    # Notes carry no schedule, but they're still linkable entities — emit them
    # (start/end empty) so they resolve in the relation index and search.
    for n in store.note_docs:
        title = n.title.strip() or _note_title_from_body(n.body) or "(untitled note)"
        items.append(ScheduledItem(home, KIND_NOTE, n.id, title, "", "", False))
    return items


def scan_disk(
    folder_paths: list[str],
    exclude_resolved: str | None = None,
) -> tuple[list[ScheduledItem], list[str]]:
    """Read scheduled items from folders **on disk** via throwaway stores.

    This is the thread-safe, off-UI-thread half of the scan: it never touches a
    live in-memory store. ``exclude_resolved`` (the open folder's resolved path)
    is skipped so it isn't double-counted — the caller reads that one from the
    live store on the UI thread. Each folder is parsed fail-soft; one that
    raises is named in ``skipped`` rather than breaking the whole scan. Folders
    resolving to the same path are visited once.

    Returns ``(items, skipped_folder_paths)``.
    """
    items: list[ScheduledItem] = []
    skipped: list[str] = []
    seen: set[str] = set()
    if exclude_resolved:
        seen.add(exclude_resolved)

    for raw in folder_paths:
        if not isinstance(raw, str) or not raw:
            continue
        rp = _resolved(raw)
        if rp in seen:
            continue
        seen.add(rp)
        store = _safe_store(Path(raw))
        if store is None:
            skipped.append(raw)
            continue
        items.extend(items_from_store(store))
    return items, skipped


def collect_items(
    live_store: ProjectStore | None,
    folder_paths: list[str],
) -> tuple[list[ScheduledItem], list[str]]:
    """Aggregate items across ``folder_paths`` (the tracked universe).

    Synchronous convenience used in tests and headless contexts: reads the open
    folder from ``live_store`` (its in-memory state is the source of truth) and
    every other folder from disk via :func:`scan_disk`. In the live app the two
    halves are split across threads (live on the UI thread, disk on a worker);
    see ``MainWindow._rescan_calendar``.

    Returns ``(items, skipped_folder_paths)``.
    """
    live_items: list[ScheduledItem] = []
    exclude: str | None = None
    if live_store is not None:
        live_items = items_from_store(live_store)
        exclude = _resolved(str(live_store.root))
    disk_items, skipped = scan_disk(folder_paths, exclude)
    return live_items + disk_items, skipped


# ── writing a schedule back to the right store ───────────────────────────────

def apply_dates(
    live_store: ProjectStore | None,
    item: ScheduledItem,
    start: str,
    end: str,
) -> bool:
    """Persist ``start``/``end`` for ``item``, routing to the correct store.

    The open folder goes through ``live_store`` (so its next save can't clobber
    the write); any other folder is opened, mutated, and saved on the spot.
    Covers projects living in the ``_orphans`` bucket. Normalizes the range
    (swaps if reversed; empty ``start`` clears both = unschedule). Returns True
    on a persisted change.
    """
    start = start or ""
    end = end or ""
    if not start:
        end = ""
    else:
        if not end:
            end = start
        if end < start:
            start, end = end, start

    home = Path(item.home)
    is_live = live_store is not None and _same_path(home, live_store.root)
    store = live_store if is_live else _safe_store(home)
    if store is None:
        return False
    if not _set_on_store(store, item.kind, item.key, start, end):
        return False
    try:
        store.save()
    except OSError:
        return False
    # Keep the in-hand item consistent with what we persisted.
    item.start, item.end = start, end
    return True


def _set_on_store(store: ProjectStore, kind: str, key: str, start: str, end: str) -> bool:
    """Mutate the matching item in ``store`` (no save). False if not found."""
    if kind == KIND_PROJECT:
        p = store.projects.get(key)
        if p is not None:
            p.start, p.end = start, end
            return True
        od = store.orphans.get(key)
        if isinstance(od, dict):
            od["start"], od["end"] = start, end
            return True
        return False
    if kind == KIND_PLAYLIST:
        pl = store.get_playlist(key)
        if pl is None:
            return False
        pl.start, pl.end = start, end
        return True
    if kind == KIND_TODO:
        t = store.get_todo(key)
        if t is None:
            return False
        t.start, t.end = start, end
        return True
    return False


# ── pure month layout (no Qt) ────────────────────────────────────────────────

@dataclass
class LayoutBar:
    """One drawable segment of a (possibly multi-week) item within the grid.

    ``week`` is the grid row (0..5); ``col_start``/``col_end`` are inclusive
    columns (0..6). ``lane`` is the vertical slot within the week, packed so no
    two bars in a lane overlap. ``continues_left``/``continues_right`` mark
    where the item extends beyond this segment (previous/next week or off the
    visible window) so the painter can draw a clipped/arrow edge.
    """

    item_index: int
    week: int
    col_start: int
    col_end: int
    lane: int
    continues_left: bool
    continues_right: bool


def month_grid_start(year: int, month: int, week_start: int = WEEK_START) -> date:
    """Date of the top-left cell of a 6×7 grid for ``year``/``month``.

    The grid always has 6 rows (42 days), which is enough to contain any month
    regardless of how it falls across weeks, giving a stable layout.
    """
    first = date(year, month, 1)
    offset = (first.weekday() - week_start) % 7
    return first - timedelta(days=offset)


GRID_DAYS = 42  # 6 weeks × 7 days


def layout_month(items: list[ScheduledItem], grid_start: date) -> list[LayoutBar]:
    """Lay scheduled ``items`` onto the 6×7 grid beginning at ``grid_start``.

    Pure and Qt-free. Each item overlapping the visible window is clipped to it,
    split at week boundaries into per-row segments, and greedily lane-packed
    within its week (earliest start first, lowest free lane). ``item_index``
    indexes back into ``items``. Unscheduled items are ignored.
    """
    grid_end = grid_start + timedelta(days=GRID_DAYS - 1)
    week_segments: list[list[LayoutBar]] = [[] for _ in range(6)]

    for idx, it in enumerate(items):
        s = parse_date(it.start)
        if s is None:
            continue
        e = parse_date(it.end) or s
        if e < s:
            s, e = e, s
        seg_start = max(s, grid_start)
        seg_end = min(e, grid_end)
        if seg_end < seg_start:
            continue  # entirely outside the visible window

        d0 = (seg_start - grid_start).days
        d1 = (seg_end - grid_start).days
        w0, w1 = d0 // 7, d1 // 7
        for w in range(w0, w1 + 1):
            col_start = 0 if w > w0 else d0 % 7
            col_end = 6 if w < w1 else d1 % 7
            cell_start = grid_start + timedelta(days=w * 7 + col_start)
            cell_end = grid_start + timedelta(days=w * 7 + col_end)
            week_segments[w].append(LayoutBar(
                item_index=idx, week=w, col_start=col_start, col_end=col_end,
                lane=-1,  # assigned below
                continues_left=s < cell_start,
                continues_right=e > cell_end,
            ))

    bars: list[LayoutBar] = []
    for w in range(6):
        # Earliest start first; among equal starts, longer segments first so a
        # multi-day bar takes a low lane and stays visible across its whole span
        # (rather than being buried — and thus "+N more"'d — by single-day items
        # that happen to share its start day).
        segs = sorted(week_segments[w],
                      key=lambda b: (b.col_start, b.col_start - b.col_end, b.col_end))
        lane_last_col: list[int] = []  # lane_last_col[lane] = last occupied column
        for seg in segs:
            placed = False
            for lane in range(len(lane_last_col)):
                if lane_last_col[lane] < seg.col_start:
                    lane_last_col[lane] = seg.col_end
                    seg.lane = lane
                    placed = True
                    break
            if not placed:
                seg.lane = len(lane_last_col)
                lane_last_col.append(seg.col_end)
            bars.append(seg)
    return bars


def items_on_day(items: list[ScheduledItem], day: date) -> list[ScheduledItem]:
    """Scheduled items whose inclusive [start, end] range contains ``day``."""
    out: list[ScheduledItem] = []
    for it in items:
        s = parse_date(it.start)
        if s is None:
            continue
        e = parse_date(it.end) or s
        if e < s:
            s, e = e, s
        if s <= day <= e:
            out.append(it)
    return out


def unscheduled(items: list[ScheduledItem]) -> list[ScheduledItem]:
    """Items with no start date — the Unscheduled tray's contents."""
    return [it for it in items if not it.scheduled]


# ── path identity helpers ────────────────────────────────────────────────────

def resolved_path(path_str: str) -> str:
    """Public canonical-path helper, so callers compute the same exclude key
    that :func:`scan_disk` dedups with (a mismatch would double-count a folder)."""
    return _resolved(path_str)


def _resolved(path_str: str) -> str:
    """Canonical string form for identity/dedup (resolve, fail-soft)."""
    try:
        return str(Path(path_str).resolve())
    except OSError:
        return str(Path(path_str))


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def _safe_store(home: Path) -> ProjectStore | None:
    try:
        return ProjectStore(home)
    except Exception:
        return None
