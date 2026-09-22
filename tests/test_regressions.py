"""Regression coverage for persistence, refresh and asynchronous UI identity."""
import json
import os

import pytest

from projectum.app import load_state, state_path
from projectum.links import LinkStore
from projectum.store import ProjectStore


def playlist(window, url="https://example.test/list", ids=("v",)):
    pl = window.store.add_playlist(url, {"title": "Playlist", "videos": [
        {"id": vid, "title": vid, "url": "https://example.test/video"} for vid in ids
    ]})
    window._rebuild_playlists_list()
    window.playlists_list_widget.setCurrentItem(window._playlist_items[pl.id])
    return pl


def test_duplicate_video_occurrences_edit_independently(window, tmp_path):
    window.load_folder(tmp_path)
    pl = playlist(window, ids=("v", "v"))
    second = window.video_list_widget.item(1)
    window.video_list_widget.setCurrentItem(second)
    assert window.current_video is pl.videos[1]
    row = window.video_list_widget.itemWidget(second)
    row.completion_changed.emit(True)
    window.video_notes_edit.setPlainText("second occurrence")
    window._flush_pending_writes()
    loaded = ProjectStore(tmp_path).playlists[0]
    assert [(v.completed, v.notes) for v in loaded.videos] == [
        (False, ""), (True, "second occurrence")]


def test_refresh_clears_video_selection_before_rebuilding(window, tmp_path):
    window.load_folder(tmp_path)
    pl = playlist(window)
    window.video_list_widget.setCurrentRow(0)
    window._on_refresh_done(pl.url, {"title": "New", "videos": [{"id": "v"}]},
                            None, refreshed_id=pl.id)
    assert window.current_video is None


@pytest.mark.parametrize("failed", [False, True])
def test_old_fetch_cannot_consume_new_request(window, tmp_path, monkeypatch, failed):
    queued = []
    monkeypatch.setattr(window, "_size_pool", type("Pool", (), {
        "start": lambda self, runnable: queued.append(runnable)})())
    window.load_folder(tmp_path)
    calls = []
    url = "https://example.test/list"
    window._kick_fetch(url, lambda *args: calls.append("old"))
    old = queued[-1]
    other = tmp_path / "other"
    other.mkdir()
    window.load_folder(other)
    window._kick_fetch(url, lambda *args: calls.append("new"))
    new = queued[-1]
    if failed:
        old.signals.failed.emit(url, "old failure")
    else:
        old.signals.done.emit(url, {"title": "old data"})
    assert calls == []
    new.signals.done.emit(url, {"title": "new data"})
    assert calls == ["new"]


def test_refresh_targets_playlist_id_even_when_urls_repeat(window, tmp_path):
    window.load_folder(tmp_path)
    a = playlist(window)
    b = playlist(window)
    window._on_refresh_done(b.url, {"title": "B updated", "videos": []}, None,
                            refreshed_id=b.id)
    assert a.title == "Playlist"
    assert b.title == "B updated"


def test_refresh_does_not_replace_pending_request(window, tmp_path, monkeypatch):
    window.load_folder(tmp_path)
    playlist(window)
    queued = []
    monkeypatch.setattr(window, "_size_pool", type("Pool", (), {
        "start": lambda self, runnable: queued.append(runnable)})())
    window._refresh_current_playlist()
    window._refresh_current_playlist()
    assert len(queued) == 1


def test_disk_note_changes_refresh_editor_and_deletion(window, tmp_path):
    window.load_folder(tmp_path)
    window._add_note()
    nid = window.current_note.id
    disk = ProjectStore(tmp_path)
    disk.get_note(nid).body = "changed externally"
    disk.save()
    window.refresh()
    assert window.note_body_edit.toPlainText() == "changed externally"
    disk.remove_note(nid)
    window.refresh()
    assert window.current_note is None
    assert window.notes_list_widget.count() == 0
    assert window.note_detail_stack.currentIndex() == 0


def test_disk_playlist_changes_refresh_details_and_deletion(window, tmp_path):
    window.load_folder(tmp_path)
    pl = playlist(window)
    disk = ProjectStore(tmp_path)
    disk.playlists[0].title = "updated externally"
    disk.save()
    window.refresh()
    assert window.current_playlist is pl
    assert window.playlist_title_label.text() == "updated externally"
    disk.remove_playlist(pl.id)
    window.refresh()
    assert window.current_playlist is None


def test_deleted_selected_project_clears_detail(window, tmp_path):
    (tmp_path / "project").mkdir()
    window.load_folder(tmp_path)
    window.list_widget.setCurrentItem(window._row_items["project"])
    (tmp_path / "project").rmdir()
    window.refresh()
    assert window.current_project is None
    assert window.detail_stack.currentIndex() == 0



@pytest.mark.parametrize("value", [[], 1, None, "text"])
def test_state_non_object_is_ignored(tmp_path, monkeypatch, value):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = state_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value))
    assert load_state() == {}


@pytest.mark.parametrize("value", [12, True])
def test_link_store_invalid_collection(tmp_path, value):
    path = tmp_path / "links.json"
    path.write_text(json.dumps({"links": value}))
    assert LinkStore(path).all_edges() == []


def test_store_malformed_ids_durations_and_collections(tmp_path):
    (tmp_path / ".projectum.json").write_text(json.dumps({
        "playlists": [{"id": [1], "videos": 12}, {"id": "p", "videos": [
            {"id": [1]}, {"id": "v", "duration": "bad"}]}],
        "todos": [{"id": {"bad": True}, "position": float("inf")}],
        "note_docs": [{"id": [1]}],
    }))
    store = ProjectStore(tmp_path)
    store.load()
    assert all(isinstance(p.id, str) for p in store.playlists)
    assert store.playlists[0].videos == []
    assert store.playlists[1].videos[0].duration is None
    assert store.todos[0].position == 0
    store.save()


def test_bad_encoding_does_not_crash_loaders(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = state_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff")
    (tmp_path / ".projectum.json").write_bytes(b"\xff")
    link_path = tmp_path / "links.json"
    link_path.write_bytes(b"\xff")
    assert load_state() == {}
    assert ProjectStore(tmp_path).playlists == []
    assert LinkStore(link_path).all_edges() == []


def test_virtualenv_is_not_externally_managed(tmp_path, monkeypatch):
    import projectum.update as update
    (tmp_path / "EXTERNALLY-MANAGED").touch()
    monkeypatch.setattr(update.sysconfig, "get_path", lambda key: str(tmp_path))
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(update.sys, "base_prefix", str(tmp_path))
    assert not update._is_externally_managed()


def test_appimage_download_failure_closes_temp_descriptor(tmp_path, monkeypatch):
    import projectum.update as update
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "appimage"))
    real_mkstemp = update.tempfile.mkstemp
    opened = []

    def mkstemp(**kwargs):
        result = real_mkstemp(**kwargs)
        opened.append(result)
        return result

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(update.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(update.urllib.request, "urlopen", fail)
    with pytest.raises(OSError, match="offline"):
        update._apply_appimage("v1")
    fd, name = opened[0]
    try:
        with pytest.raises(OSError):
            os.fstat(fd)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    assert not os.path.exists(name)


def test_old_metadata_probe_cannot_update_same_named_project(window, tmp_path, monkeypatch):
    from projectum.widgets import SizeRunnable, GitRunnable
    queued = []
    monkeypatch.setattr(window, "_size_pool", type("Pool", (), {
        "start": lambda self, runnable: queued.append(runnable)})())
    for folder in ("a", "b"):
        root = tmp_path / folder
        (root / "project").mkdir(parents=True)
        window.load_folder(root)
        window.list_widget.setCurrentItem(window._row_items["project"])
    sizes = [r for r in queued if isinstance(r, SizeRunnable)]
    gits = [r for r in queued if isinstance(r, GitRunnable)]
    sizes[-1].signals.done.emit(sizes[-1].project_name, 222)
    gits[-1].signals.done.emit(gits[-1].project_name, {"branch": "new", "dirty": False})
    sizes[0].signals.done.emit(sizes[0].project_name, 999)
    gits[0].signals.done.emit(gits[0].project_name, {"branch": "old", "dirty": True})
    assert window.size_box._value.text() == window._format_size(222)
    assert window.git_box._value.text() == "new"


@pytest.mark.parametrize("end", ["2026-09-24", "2026-09-22"])
def test_calendar_remove_handles_date_ranges(window, tmp_path, end):
    from projectum.links import make_ref, daterange_ref
    window.load_folder(tmp_path)
    ref = make_ref("todo", str(tmp_path), "todo")
    span = daterange_ref("2026-09-22", end)
    window._link_store.add(ref, span)
    window._unlink_date(ref, "2026-09-22", end)
    assert not window._link_store.has(ref, span)


@pytest.mark.parametrize("value", ["-3 days", "1.5h", "1h garbage", "tomorrow 2h", "1h 30"])
def test_duration_rejects_partial_matches(value):
    from projectum.links import parse_delta
    assert parse_delta(value) is None


def test_malformed_filesystem_ids_and_orphan_tags(tmp_path):
    store = ProjectStore(tmp_path, prior_fsids={"gone": [[], 2, 3]})
    store.orphans["gone"] = {"tags": [{"bad": "tag"}]}
    store.tag_colors["unused"] = "#ffffff"
    assert store.prune_unused_tag_colors() == ["unused"]
