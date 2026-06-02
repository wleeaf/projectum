"""Data-layer tests for ProjectStore — pure stdlib, no Qt.

These lock in the round-trip fidelity and the specific regressions fixed in
1.1.0 (orphan pin/position, duplicate video ids, corrupt-JSON resilience, the
settings-state contract, etc.).
"""

import json

from projectum.store import ProjectStore, Todo, Video


def _store(tmp_path, *projects):
    for p in projects:
        (tmp_path / p).mkdir()
    return ProjectStore(tmp_path)


def _write_raw(tmp_path, payload):
    (tmp_path / ".projectum.json").write_text(json.dumps(payload), encoding="utf-8")


# ── projects round-trip ──

def test_project_roundtrip(tmp_path):
    s = _store(tmp_path, "alpha", "beta")
    s.projects["alpha"].completed = True
    s.projects["alpha"].notes = "hello"
    s.projects["alpha"].tags = ["x", "y"]
    s.projects["alpha"].pinned = True
    s.projects["alpha"].position = 3
    s.projects["alpha"].tested = True
    s.save()

    s2 = ProjectStore(tmp_path)
    a = s2.projects["alpha"]
    assert (a.completed, a.notes, a.tags, a.pinned, a.position, a.tested) == (
        True, "hello", ["x", "y"], True, 3, True,
    )


def test_sorted_projects_pinned_first(tmp_path):
    s = _store(tmp_path, "a", "b", "c")
    s.projects["c"].pinned = True
    assert s.sorted_projects()[0].name == "c"


# ── orphans: the 1.1.0 pin/position regression ──

def test_orphan_preserves_pin_and_position(tmp_path):
    s = _store(tmp_path, "proj")
    s.projects["proj"].pinned = True
    s.projects["proj"].position = 7
    s.save()
    (tmp_path / "proj").rmdir()        # folder disappears
    s.load(); s.save()                  # watcher cycle drops it from active
    del s
    (tmp_path / "proj").mkdir()        # folder returns
    s2 = ProjectStore(tmp_path)
    assert s2.projects["proj"].pinned is True
    assert s2.projects["proj"].position == 7


def test_orphan_preserves_notes_tags(tmp_path):
    s = _store(tmp_path, "p")
    s.projects["p"].notes = "keep"; s.projects["p"].tags = ["t"]; s.save()
    (tmp_path / "p").rmdir(); s.load()
    assert "p" in s.orphans
    (tmp_path / "p").mkdir(); s.load()
    assert s.projects["p"].notes == "keep" and s.projects["p"].tags == ["t"]


# ── playlists: merge_fetch + duplicate video ids ──

def test_merge_fetch_preserves_state_and_flags_unavailable(tmp_path):
    s = ProjectStore(tmp_path)
    pl = s.add_playlist("u", {"title": "t", "videos": [
        {"id": "v1", "title": "1", "url": "a"},
        {"id": "v2", "title": "2", "url": "b"},
    ]})
    pl.videos[0].completed = True
    pl.videos[0].notes = "watched"
    pl.merge_fetch({"title": "t2", "videos": [
        {"id": "v2", "title": "2x", "url": "b"},
        {"id": "v3", "title": "3", "url": "c"},
    ]})
    by_id = {v.id: v for v in pl.videos}
    assert by_id["v1"].unavailable and by_id["v1"].completed and by_id["v1"].notes == "watched"
    assert by_id["v2"].unavailable is False
    assert by_id["v3"].unavailable is False


def test_duplicate_video_ids_kept_distinct(tmp_path):
    s = ProjectStore(tmp_path)
    pl = s.add_playlist("u", {"title": "t", "videos": [
        {"id": "dup", "title": "A", "url": "a"},
        {"id": "dup", "title": "B", "url": "b"},
    ]})
    pl.videos[0].notes = "first"
    pl.videos[1].notes = "second"
    s.save(); s.load()
    v = s.playlists[0].videos
    assert len(v) == 2 and v[0] is not v[1]
    assert v[0].notes == "first" and v[1].notes == "second"


# ── todos ──

def test_todo_crud_and_order(tmp_path):
    s = ProjectStore(tmp_path)
    a = s.add_todo("one"); b = s.add_todo("two"); c = s.add_todo("three")
    assert [t.text for t in s.sorted_todos()] == ["one", "two", "three"]
    assert (a.position, b.position, c.position) == (0, 1, 2)
    b.done = True; s.save()

    s2 = ProjectStore(tmp_path)
    assert s2.todo_stats() == (1, 3)
    s2.reorder_todos([c.id, a.id, b.id]); s2.save()

    s3 = ProjectStore(tmp_path)
    assert [t.text for t in s3.sorted_todos()] == ["three", "one", "two"]
    assert s3.remove_todo(a.id) is True
    assert s3.get_todo(a.id) is None


def test_todo_instance_preserved_across_reload(tmp_path):
    s = ProjectStore(tmp_path)
    s.add_todo("keep me");
    inst = s.todos[0]
    s.load()
    assert s.todos[0] is inst  # callers/rows holding the reference stay valid


# ── corrupt / hand-edited JSON resilience ──

def test_corrupt_json_variants_do_not_crash(tmp_path):
    (tmp_path / "p").mkdir()
    for payload in (
        [1, 2, 3],                                  # top-level not a dict
        {"projects": [1, 2, 3]},                     # projects not a dict
        {"projects": {"p": {"position": None}}},     # non-int position
        {"projects": {"p": {"tags": "abc"}}},        # string tags
        {"playlists": {"x": "y"}},                   # playlists not a list
        {"todos": "garbage"},                        # todos not a list
        {"tag_colors": [1, 2]},                      # tag_colors not a dict
    ):
        _write_raw(tmp_path, payload)
        ProjectStore(tmp_path)  # must not raise


def test_string_tags_not_split_into_chars(tmp_path):
    (tmp_path / "p").mkdir()
    _write_raw(tmp_path, {"projects": {"p": {"tags": "abc"}}})
    assert ProjectStore(tmp_path).projects["p"].tags == []


def test_nonstr_title_coerced(tmp_path):
    _write_raw(tmp_path, {"playlists": [
        {"id": "i", "title": 123, "url": 456,
         "videos": [{"id": "v", "title": 789, "url": 0}]}
    ]})
    pl = ProjectStore(tmp_path).playlists[0]
    assert isinstance(pl.title, str) and isinstance(pl.videos[0].title, str)


# ── atomic save ──

def test_save_is_atomic_no_tmp_left(tmp_path):
    s = _store(tmp_path, "a")
    s.save()
    assert (tmp_path / ".projectum.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


# ── tag-color pruning ──

def test_prune_unused_tag_colors(tmp_path):
    s = _store(tmp_path, "p")
    s.projects["p"].tags = ["keep"]
    s.tag_colors = {"keep": "#fff", "gone": "#000"}
    removed = s.prune_unused_tag_colors()
    assert removed == ["gone"] and "gone" not in s.tag_colors


# ── dataclass sanity ──

def test_video_and_todo_defaults():
    assert Video(id="x", title="t", url="u").completed is False
    assert Todo(id="x", text="t").done is False
