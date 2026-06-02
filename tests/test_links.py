"""Relations: EntityRef identity + global LinkStore (pure, no Qt)."""

import json

from projectum.links import (
    EntityRef, LinkStore, daterange_ref, date_ref, delta_from_unit, delta_ref,
    format_delta, index_entities, make_ref, parse_daterange, parse_delta,
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


def test_daterange_ref_and_parse():
    dr = daterange_ref("2026-06-10", "2026-06-03")     # reversed -> ordered key
    assert dr.key == "2026-06-03..2026-06-10"
    assert dr.is_calendar and not dr.is_date and dr.is_temporal
    assert parse_daterange(dr.key) == ("2026-06-03", "2026-06-10")
    assert parse_daterange("not-a-range") is None


def test_delta_parse_format_and_dedup():
    assert parse_delta("3 days") == 4320 and parse_delta("3d") == 4320
    assert parse_delta("2w") == 20160 and parse_delta("1d 4h") == 1680
    assert parse_delta("90m") == 90 and parse_delta("garbage") is None
    assert delta_ref("1 week").key == delta_ref("7 days").key   # same duration -> one node
    assert delta_ref("nonsense") is None
    d = delta_ref("2 weeks")
    assert d.kind == "delta" and d.is_temporal and not d.is_calendar
    assert format_delta(4320) == "3 days" and format_delta(20160) == "2 weeks"
    assert format_delta(90) == "1h 30m"
    # the picker's count+unit builder, and month formatting
    assert delta_from_unit(3, "weeks").key == delta_ref("3 weeks").key
    assert delta_from_unit(0, "days") is None and delta_from_unit(1, "bogus") is None
    assert format_delta(43200) == "1 month" and format_delta(129600) == "3 months"


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


def test_links_dialog_add_delta_duration(qapp, tmp_path):
    from projectum.widgets import LinksDialog
    store = LinkStore(tmp_path / "l.json")
    subj = make_ref("project", "/r/A", "alpha")
    dlg = LinksDialog(subj, "Alpha", store, {})
    dlg._delta_count.setValue(3)
    dlg._delta_unit.setCurrentText("days")
    dlg._add_delta()
    neigh = store.neighbors(subj)
    assert len(neigh) == 1 and neigh[0].kind == "delta"
    assert format_delta(int(neigh[0].key)) == "3 days"
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


def test_links_dialog_open_emits_navigate(qapp, tmp_path):
    from projectum.widgets import LinksDialog
    store = LinkStore(tmp_path / "l.json")
    subj = make_ref("todo", "/r/A", "u1")
    other = make_ref("project", "/r/A", "alpha")
    store.add(subj, other)
    dlg = LinksDialog(subj, "T", store, index_entities([("/r/A", "project", "alpha", "Alpha")]))
    got = []
    dlg.navigate.connect(lambda r: got.append(r))
    dlg._open(other)
    assert got == [other]


def test_navigate_to_cross_folder(window, qapp, tmp_path):
    fa = tmp_path / "A"; fa.mkdir(); (fa / "alpha").mkdir()
    fb = tmp_path / "B"; fb.mkdir(); (fb / "beta").mkdir()
    ProjectStore(fa).save(); ProjectStore(fb).save()
    from projectum.app import load_state, save_state
    st = load_state(); st["recent_folders"] = [str(fa), str(fb)]; save_state(st)
    window.load_folder(fa)
    assert window.store.root.name == "A"
    window._navigate_to(make_ref("project", str(fb), "beta"))   # project in the OTHER folder
    qapp.processEvents()
    assert window.store.root.name == "B"        # switched folders
    assert window.current_tab == "projects"


def test_prune_links_on_todo_delete(window, qapp, tmp_path):
    fa = tmp_path / "work"; fa.mkdir()
    s = ProjectStore(fa); todo = s.add_todo("doomed"); s.save()
    window.load_folder(fa)
    ref = make_ref("todo", str(fa), todo.id)
    window._link_store.add(ref, date_ref("2026-06-09"))
    assert window._link_store.degree(ref) == 1
    window._remove_todo(todo.id)                 # explicit deletion prunes edges
    assert window._link_store.degree(ref) == 0


# ── graph view ──

def test_graph_canvas_layout_and_hit(qapp, tmp_path):
    from projectum.widgets import GraphCanvas
    store = LinkStore(tmp_path / "l.json")
    hub = make_ref("todo", "/r", "h")
    store.add(hub, make_ref("project", "/r", "p1"))
    store.add(hub, date_ref("2026-06-20"))
    c = GraphCanvas(); c.resize(600, 600)
    c.set_data(store, index_entities([("/r", "todo", "h", "Hub"), ("/r", "project", "p1", "P1")]))
    c.set_focus(hub)
    c._layout()
    assert len(c._nodes) == 3 and c._nodes[0][0] == hub      # focus + 2 neighbours
    assert c._node_at(c._nodes[0][1]) == hub                 # hit-test the focus node
    assert c._node_at(c._nodes[1][1]) == c._nodes[1][0]      # hit-test a neighbour
    c.deleteLater()


def test_graph_view_focus_via_completer(qapp, tmp_path):
    from projectum.widgets import GraphView
    gv = GraphView()
    gv.set_data(LinkStore(tmp_path / "l.json"),
                index_entities([("/r", "project", "alpha", "Alpha service")]))
    label = "Alpha service · Project"
    assert label in gv._title_to_ref
    gv._focus_from_completer(label)
    assert gv.canvas.focus() == make_ref("project", "/r", "alpha")
    gv.deleteLater()
