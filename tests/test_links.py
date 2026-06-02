"""Relations: EntityRef identity + global LinkStore (pure, no Qt)."""

import json

from projectum.links import (
    EntityRef, LinkStore, date_ref, index_entities, make_ref,
)
from projectum.store import ProjectStore


def test_entityref_roundtrip_and_identity():
    r = make_ref("todo", "/tmp/work/", "u1")          # trailing slash...
    r2 = make_ref("todo", "/tmp/work", "u1")          # ...resolves to same identity
    assert r == r2 and hash(r) == hash(r2)
    assert EntityRef.from_list(r.as_list()) == r
    assert EntityRef.from_list(["bad"]) is None
    d = date_ref("2026-06-03")
    assert d.is_date and d.home == "" and d.key == "2026-06-03"


def test_linkstore_add_dedup_undirected(tmp_path):
    s = LinkStore(tmp_path / "links.json")
    a = make_ref("todo", "/r/A", "u1")
    b = make_ref("playlist", "/r/B", "p1")
    assert s.add(a, b) is True
    assert s.add(b, a) is False          # same undirected edge -> dedup
    assert s.has(a, b) and s.has(b, a)   # symmetric
    assert s.neighbors(a) == [b] and s.neighbors(b) == [a]
    assert s.add(a, a) is False          # no self-loops
    assert s.degree(a) == 1


def test_linkstore_persistence(tmp_path):
    p = tmp_path / "links.json"
    s = LinkStore(p)
    a, b, d = make_ref("todo", "/r/A", "u1"), make_ref("project", "/r/A", "alpha"), date_ref("2026-06-10")
    s.add(a, b)
    s.add(a, d)                          # entity <-> date
    # reload from disk
    s2 = LinkStore(p)
    assert set(s2.neighbors(a)) == {b, d}
    assert s2.neighbors(d) == [a]        # date side sees the backlink
    raw = json.loads(p.read_text())
    assert raw["version"] == 1 and len(raw["links"]) == 2


def test_linkstore_remove_and_remove_entity(tmp_path):
    s = LinkStore(tmp_path / "links.json")
    a, b, c = (make_ref("todo", "/r", "a"), make_ref("todo", "/r", "b"),
               make_ref("todo", "/r", "c"))
    s.add(a, b); s.add(a, c); s.add(b, c)
    assert s.remove(a, b) is True and s.has(a, b) is False
    assert s.remove(a, b) is False       # already gone
    # deleting entity c drops all its edges (explicit-deletion pruning)
    removed = s.remove_entity(c)
    assert removed == 2                  # (a,c) and (b,c)
    assert s.neighbors(c) == [] and s.degree(a) == 0


def test_linkstore_failsoft_on_corrupt(tmp_path):
    p = tmp_path / "links.json"
    p.write_text("{ not valid json ")
    s = LinkStore(p)                     # must not raise
    assert s.all_edges() == []
    # a structurally-wrong entry is skipped, valid ones kept
    p.write_text(json.dumps({"links": [["x"], [["todo", "/r", "a"], ["todo", "/r", "b"]]]}))
    s2 = LinkStore(p)
    assert len(s2.all_edges()) == 1


def test_index_entities():
    idx = index_entities([
        ("/r/A", "todo", "u1", "Write tests"),
        ("/r/A", "project", "alpha", "Alpha"),
        ("/r/A", "playlist", "p1", ""),     # empty title -> placeholder
    ])
    assert idx[make_ref("todo", "/r/A", "u1")].title == "Write tests"
    assert idx[make_ref("playlist", "/r/A", "p1")].title == "(untitled)"


# ── dialog + app wiring ──

def test_links_dialog_add_search_date_and_remove(qapp, tmp_path):
    from projectum.widgets import LinksDialog
    store = LinkStore(tmp_path / "links.json")
    subject = make_ref("todo", "/r/A", "u1")
    index = index_entities([("/r/B", "playlist", "p1", "Rust deep dive")])
    changed = []
    dlg = LinksDialog(subject, "Ship it", store, index)
    dlg.changed.connect(lambda: changed.append(1))

    dlg._on_search("rust")                       # filters the index
    assert dlg._results.count() == 1
    dlg._add_from_result(dlg._results.item(0))   # add the playlist link
    pl = make_ref("playlist", "/r/B", "p1")
    assert store.has(subject, pl) and changed

    dlg._add_date()                              # add a date link (today)
    assert any(r.is_date for r in store.neighbors(subject))

    dlg._remove(pl)                              # remove via the dialog
    assert not store.has(subject, pl)
    dlg.deleteLater()


def test_open_links_dialog_indexes_cross_folder(window, qapp, tmp_path):
    fa = tmp_path / "work"; fa.mkdir(); (fa / "alpha").mkdir()
    fb = tmp_path / "media"; fb.mkdir(); (fb / "beta").mkdir()
    ProjectStore(fa).save()
    sb = ProjectStore(fb); pl = sb.add_playlist("http://x", {"title": "X", "videos": []}); sb.save()
    # track both, open A
    from projectum.app import load_state, save_state
    st = load_state(); st["recent_folders"] = [str(fa), str(fb)]; save_state(st)
    window.load_folder(fa)
    win_ref = make_ref("project", str(fa), "alpha")
    window._open_links_dialog(win_ref, "alpha")
    qapp.processEvents()
    # the cross-folder playlist in B is resolvable for linking
    assert make_ref("playlist", str(fb), pl.id) in window._links_dialog._index
    window._links_dialog.close()


def test_prune_links_on_todo_delete(window, qapp, tmp_path):
    fa = tmp_path / "work"; fa.mkdir()
    s = ProjectStore(fa); todo = s.add_todo("doomed"); s.save()
    window.load_folder(fa)
    ref = make_ref("todo", str(fa), todo.id)
    window._link_store.add(ref, date_ref("2026-06-09"))
    assert window._link_store.degree(ref) == 1
    window._remove_todo(todo.id)                 # explicit deletion prunes edges
    assert window._link_store.degree(ref) == 0
