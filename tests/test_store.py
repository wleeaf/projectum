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


# ── folder expansion: nested subfolders as projects ──

def _nested(tmp_path):
    (tmp_path / "web-client").mkdir()
    (tmp_path / "freelance").mkdir()
    (tmp_path / "freelance" / "acme").mkdir()
    (tmp_path / "freelance" / "acme" / "api").mkdir()
    (tmp_path / "freelance" / "globex").mkdir()
    return ProjectStore(tmp_path)


def test_expansion_zero_is_classic_flat(tmp_path):
    s = _nested(tmp_path)                       # no expansions
    assert [p.name for p in s.sorted_projects()] == ["freelance", "web-client"]
    assert all(p.depth == 0 for p in s.sorted_projects())


def test_expansion_adds_nested_projects_by_depth(tmp_path):
    s = _nested(tmp_path)
    s.set_expansion("freelance", 1)            # immediate children only
    assert [p.name for p in s.sorted_projects()] == [
        "freelance", "freelance/acme", "freelance/globex", "web-client"]
    assert s.projects["freelance/acme"].depth == 1
    assert s.projects["freelance/acme"].leaf == "acme"

    s.set_expansion("freelance", 2)            # one level deeper
    assert "freelance/acme/api" in s.projects
    assert s.projects["freelance/acme/api"].depth == 2


def test_expansion_tree_order_keeps_subtree_with_parent(tmp_path):
    s = _nested(tmp_path)
    s.set_expansion("freelance", 2)
    s.projects["freelance/globex"].pinned = True   # floats among its siblings
    s.save(); s.load()
    # globex floats above acme, but acme keeps its api child directly under it,
    # and the whole freelance subtree stays grouped before web-client.
    assert [p.name for p in s.sorted_projects()] == [
        "freelance", "freelance/globex", "freelance/acme",
        "freelance/acme/api", "web-client"]


def test_expansion_skips_dotfolders(tmp_path):
    s = _nested(tmp_path)
    (tmp_path / "freelance" / ".git").mkdir()
    s.set_expansion("freelance", 1)
    assert "freelance/.git" not in s.projects


def test_unexpand_preserves_metadata_via_orphans(tmp_path):
    s = _nested(tmp_path)
    s.set_expansion("freelance", 1)
    s.projects["freelance/acme"].tags = ["important"]
    s.projects["freelance/acme"].completed = True
    s.save()
    s.set_expansion("freelance", 0)            # stop expanding
    assert "freelance/acme" not in s.projects
    assert s.orphans["freelance/acme"]["tags"] == ["important"]
    s.set_expansion("freelance", 1)            # re-expand -> restored
    assert s.projects["freelance/acme"].tags == ["important"]
    assert s.projects["freelance/acme"].completed is True


def test_expansion_persists_round_trip(tmp_path):
    s = _nested(tmp_path)
    s.set_expansion("freelance", 2)
    s2 = ProjectStore(tmp_path)                 # fresh load reads expansions
    assert s2.expansions == {"freelance": 2}
    assert "freelance/acme/api" in s2.projects


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


# ── notes (folder-wide Notes tab) ──

def test_note_crud_and_order(tmp_path):
    s = ProjectStore(tmp_path)
    a = s.add_note("First", "body a")
    b = s.add_note("Second", "body b")
    c = s.add_note("Third", "body c")
    assert [n.title for n in s.sorted_notes()] == ["First", "Second", "Third"]
    assert (a.position, b.position, c.position) == (0, 1, 2)
    s.reorder_notes([c.id, a.id, b.id]); s.save()

    s2 = ProjectStore(tmp_path)
    assert [n.title for n in s2.sorted_notes()] == ["Third", "First", "Second"]
    assert s2.get_note(a.id).body == "body a"
    assert s2.remove_note(a.id) is True
    assert s2.get_note(a.id) is None

    # Persisted under note_docs; the legacy scalar key is not written.
    raw = json.loads((tmp_path / ".projectum.json").read_text())
    assert "note_docs" in raw and "notes" not in raw


def test_note_migrates_legacy_scratchpad_once(tmp_path):
    _write_raw(tmp_path, {"version": 2, "notes": "# Old\nbody line"})
    s = ProjectStore(tmp_path)
    assert len(s.note_docs) == 1
    assert s.note_docs[0].title == "Old"          # leading '#' stripped
    assert s.note_docs[0].body == "# Old\nbody line"
    s.save()
    # After migration the legacy string is dropped; reload must not duplicate.
    s2 = ProjectStore(tmp_path)
    assert len(s2.note_docs) == 1


def test_note_empty_list_does_not_remigrate(tmp_path):
    # A present-but-empty note_docs means the user cleared notes — even if a
    # stale legacy 'notes' string is also present, it must not resurrect.
    _write_raw(tmp_path, {"version": 2, "note_docs": [], "notes": "stale"})
    s = ProjectStore(tmp_path)
    assert s.note_docs == []


def test_note_instance_preserved_across_reload(tmp_path):
    s = ProjectStore(tmp_path)
    s.add_note("keep", "x")
    inst = s.note_docs[0]
    s.load()
    assert s.note_docs[0] is inst


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
