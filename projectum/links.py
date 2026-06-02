"""Universal relations: a global, untyped link graph over Projectum entities.

Every linkable thing — a project, playlist, video, todo, note, tag, or a
calendar **date** — is named by an :class:`EntityRef` ``(kind, home, key)``:

* ``home`` is the entity's tracked-folder root, **resolved** so the same folder
  named two ways (trailing slash, symlink) yields one identity.
* dates are folderless (``home == ""``), e.g. ``EntityRef("date", "", "2026-06-03")``.

Links are **undirected and untyped** ("A is related to B") and live in a single
global store, ``~/.config/projectum/links.json`` — not in any folder's
``.projectum.json``. That's a deliberate trade-off the user chose: relations can
span folders, but unlike the rest of Projectum's data they don't travel with a
folder. Because the store is one file we own, it's written on the UI thread with
atomic writes — there's no per-folder routing or write race (the cross-folder
scan stays purely read-only, used only to *resolve* refs to titles).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

KIND_DATE = "date"


def _resolved(path_str: str) -> str:
    """Canonical path form for ref identity (resolve, fail-soft)."""
    if not path_str:
        return ""
    try:
        return str(Path(path_str).resolve())
    except OSError:
        return str(Path(path_str))


@dataclass(frozen=True)
class EntityRef:
    """A stable, hashable identity for one linkable entity.

    Frozen so it can key sets/dicts and live inside edge frozensets. ``home`` is
    expected already-resolved (use :func:`make_ref` / :func:`date_ref`).
    """

    kind: str
    home: str
    key: str

    @property
    def is_date(self) -> bool:
        return self.kind == KIND_DATE

    def as_list(self) -> list[str]:
        return [self.kind, self.home, self.key]

    @staticmethod
    def from_list(x) -> "EntityRef | None":
        if (isinstance(x, list) and len(x) == 3
                and all(isinstance(i, str) for i in x)):
            return EntityRef(x[0], x[1], x[2])
        return None

    def sort_key(self) -> tuple[str, str, str]:
        return (self.kind, self.home, self.key)


def make_ref(kind: str, home: str, key: str) -> EntityRef:
    """Build a ref, resolving ``home`` so identity is canonical."""
    return EntityRef(kind, _resolved(home), key)


def date_ref(iso: str) -> EntityRef:
    return EntityRef(KIND_DATE, "", iso)


@dataclass
class EntityInfo:
    """A resolved entity for display (title + kind) behind a ref."""

    ref: EntityRef
    title: str
    kind: str


def index_entities(entities) -> dict[EntityRef, EntityInfo]:
    """Build a ref→info index from ``(home, kind, key, title)`` tuples.

    Used for the 'Add link' search and to resolve neighbour titles. Dates are
    not indexed (they're infinite and resolve to themselves).
    """
    idx: dict[EntityRef, EntityInfo] = {}
    for home, kind, key, title in entities:
        ref = make_ref(kind, home, key)
        idx[ref] = EntityInfo(ref, title or "(untitled)", kind)
    return idx


class LinkStore:
    """The global undirected link graph, persisted atomically."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._edges: set[frozenset] = set()  # each: frozenset({EntityRef, EntityRef})
        self.load()

    def load(self) -> None:
        self._edges = set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        for pair in data.get("links", []) or []:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            a = EntityRef.from_list(pair[0])
            b = EntityRef.from_list(pair[1])
            if a is not None and b is not None and a != b:
                self._edges.add(frozenset((a, b)))

    def save(self) -> None:
        out: list[list[list[str]]] = []
        for edge in self._edges:
            a, b = sorted(edge, key=lambda r: r.sort_key())
            out.append([a.as_list(), b.as_list()])
        out.sort()  # stable, diff-friendly ordering
        payload = {"version": 1, "links": out}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, a: EntityRef, b: EntityRef) -> bool:
        """Link ``a`` and ``b`` (no self-loops). True if a new edge was added."""
        if a == b:
            return False
        edge = frozenset((a, b))
        if edge in self._edges:
            return False
        self._edges.add(edge)
        self.save()
        return True

    def remove(self, a: EntityRef, b: EntityRef) -> bool:
        edge = frozenset((a, b))
        if edge in self._edges:
            self._edges.discard(edge)
            self.save()
            return True
        return False

    def has(self, a: EntityRef, b: EntityRef) -> bool:
        return frozenset((a, b)) in self._edges

    def neighbors(self, ref: EntityRef) -> list[EntityRef]:
        out: list[EntityRef] = []
        for edge in self._edges:
            if ref in edge:
                out.append(next(iter(edge - {ref})))
        return sorted(out, key=lambda r: r.sort_key())

    def degree(self, ref: EntityRef) -> int:
        return sum(1 for edge in self._edges if ref in edge)

    def remove_entity(self, ref: EntityRef) -> int:
        """Drop every edge touching ``ref`` — call on explicit in-app deletion
        so the store doesn't accumulate edges to things that are truly gone.
        Returns the number of edges removed."""
        gone = [edge for edge in self._edges if ref in edge]
        for edge in gone:
            self._edges.discard(edge)
        if gone:
            self.save()
        return len(gone)

    def all_edges(self) -> list[tuple[EntityRef, EntityRef]]:
        return [tuple(sorted(e, key=lambda r: r.sort_key())) for e in self._edges]

    def all_refs(self) -> set[EntityRef]:
        refs: set[EntityRef] = set()
        for edge in self._edges:
            refs |= edge
        return refs
