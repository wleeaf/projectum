"""Offscreen Qt tests: MainWindow boot + the headline regressions and the
Todo/scroll behavior. Run headless via QT_QPA_PLATFORM=offscreen (set in
conftest).
"""

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from projectum.app import load_state, save_state
from projectum.widgets import TodoRow


def make_folder(base: Path, *names: str) -> Path:
    root = base / "ws"
    root.mkdir(exist_ok=True)
    for n in names:
        (root / n).mkdir(exist_ok=True)
    return root


def test_boot_has_five_tabs(window):
    keys = [b.property("tab_key") for b in window._tab_group.buttons()]
    assert keys == ["projects", "playlists", "todos", "calendar", "notes"]


def test_load_folder_preserves_settings_and_geometry(window, tmp_path):
    # A prior session persisted theme/font + geometry.
    save_state({"settings": {"theme": "dracula", "font_family": "Inter",
                             "font_size": 15}, "geometry": "ab"})
    window.load_folder(make_folder(tmp_path, "a"))
    st = load_state()
    assert st.get("settings", {}).get("theme") == "dracula"
    assert st.get("geometry") == "ab"
    assert st.get("last_folder")


def test_filter_and_search_reset_across_folders(window, tmp_path):
    window.load_folder(make_folder(tmp_path, "a", "b"))
    window.search_query = "zzz"
    window.current_filter = "completed"
    window.search_input.setText("zzz")
    other = tmp_path / "ws2"; other.mkdir(); (other / "x").mkdir()
    window.load_folder(other)
    assert window.search_query == "" and window.search_input.text() == ""
    assert window.current_filter == "all"


def test_notes_flushed_and_selection_restored_on_search(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, "alpha", "beta"))
    window.list_widget.setCurrentItem(window._row_items["alpha"])
    qapp.processEvents()
    window.notes_edit.setPlainText("draft for alpha")
    window.search_query = "beta"; window._apply_filter()
    assert window.store.projects["alpha"].notes == "draft for alpha"  # flushed
    assert window.current_project is None and window._hidden_selection == "alpha"
    window.search_query = ""; window._apply_filter()
    assert window.current_project and window.current_project.name == "alpha"


def test_tag_row_height_not_squeezed_on_removal(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, "one", "two"))
    window.store.projects["one"].tags = ["solo"]
    window.store.projects["two"].tags = ["alpha", "bravo"]
    window.store.save(); window._full_rebuild_list(); qapp.processEvents()
    one_h = window._row_items["one"].sizeHint().height()
    window.list_widget.setCurrentItem(window._row_items["two"]); qapp.processEvents()
    window._remove_tag("alpha"); qapp.processEvents(); qapp.processEvents()
    two_h = window._row_items["two"].sizeHint().height()
    assert abs(two_h - one_h) <= 2  # one tag left -> ~one-tag height, not crushed


def test_refresh_merges_into_correct_playlist(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, "p"))
    # Don't hit the network: register the pending fetch but start no runnable.
    window._kick_fetch = lambda url, on_done: window._pending_fetches.__setitem__(
        url, (None, on_done))
    a = window.store.add_playlist("http://A", {"title": "A", "videos": [
        {"id": "a1", "title": "a1", "url": "x"}]})
    b = window.store.add_playlist("http://B", {"title": "B", "videos": [
        {"id": "b1", "title": "b1", "url": "y"}]})
    window._rebuild_playlists_list()
    window.playlists_list_widget.setCurrentItem(window._playlist_items[a.id])
    window._refresh_current_playlist()
    # User switches to B before A's fetch returns.
    window.playlists_list_widget.setCurrentItem(window._playlist_items[b.id])
    window._handle_fetch_done("http://A", {"title": "A2", "videos": [
        {"id": "a1", "title": "a1", "url": "x"},
        {"id": "a2", "title": "a2", "url": "z"}]})
    assert a.title == "A2" and len(a.videos) == 2     # A got the data
    assert b.title == "B" and len(b.videos) == 1      # B untouched


def test_todo_lifecycle_and_persistence(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, "p"))
    window._goto_tab("todos"); qapp.processEvents()
    assert window.todo_empty_hint.isVisible()
    for t in ("write tests", "ship it"):
        window.todo_input.setText(t); window._add_todo()
    qapp.processEvents()
    ids = [t.id for t in window.store.sorted_todos()]
    assert [t.text for t in window.store.sorted_todos()] == ["write tests", "ship it"]

    roww = window.todo_list_widget.itemWidget(window._todo_items[ids[0]])
    assert isinstance(roww, TodoRow)
    roww.toggle.setChecked(True); qapp.processEvents()
    assert window.store.get_todo(ids[0]).done is True
    assert window.todo_counter.text() == "1 of 2 done"

    roww2 = window.todo_list_widget.itemWidget(window._todo_items[ids[1]])
    roww2.begin_edit(); roww2.editor.setText("ship v1.3"); roww2._commit_edit()
    assert window.store.get_todo(ids[1]).text == "ship v1.3"

    # persisted to disk
    from projectum.store import ProjectStore
    reloaded = ProjectStore(window.store.root)
    assert [(t.text, t.done) for t in reloaded.sorted_todos()] == [
        ("write tests", True), ("ship v1.3", False)]


def test_project_expansion_shows_indented_nested_rows(window, tmp_path, qapp):
    from PySide6.QtWidgets import QListWidget
    from projectum.widgets import ProjectRow
    ws = tmp_path / "ws2"; ws.mkdir()
    (ws / "web-client").mkdir()
    (ws / "freelance").mkdir()
    (ws / "freelance" / "acme").mkdir()
    (ws / "freelance" / "globex").mkdir()
    window.load_folder(ws); qapp.processEvents()

    # Flat to start: drag-reorder on, no nested rows.
    assert window.list_widget.dragDropMode() == QListWidget.DragDropMode.InternalMove
    assert "freelance/acme" not in window._row_items

    window._set_project_expansion("freelance", 1); qapp.processEvents()
    assert {"freelance/acme", "freelance/globex"} <= set(window._row_items)
    # Drag-reorder is disabled while a folder is expanded.
    assert window.list_widget.dragDropMode() == QListWidget.DragDropMode.NoDragDrop
    # The nested row shows the leaf name and is indented.
    row = window.list_widget.itemWidget(window._row_items["freelance/acme"])
    assert isinstance(row, ProjectRow) and row.name_label.text() == "acme"
    assert row.layout().contentsMargins().left() > 12

    window._set_project_expansion("freelance", 0); qapp.processEvents()
    assert "freelance/acme" not in window._row_items
    assert window.list_widget.dragDropMode() == QListWidget.DragDropMode.InternalMove


def test_notes_tab_create_select_persist(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, "p"))
    window._goto_tab("notes"); qapp.processEvents()
    assert window.notes_empty_hint.isVisible()      # no notes yet

    window._add_note(); qapp.processEvents()
    window.note_title_edit.setText("Design")
    window.note_body_edit.setPlainText("# Design\nthink about it")
    window._note_save_timer.stop(); window._save_current_note()

    window._add_note(); qapp.processEvents()
    window.note_title_edit.setText("Ideas")
    window.note_body_edit.setPlainText("ship it")
    window._note_save_timer.stop(); window._save_current_note()
    assert window.notes_list_widget.count() == 2

    # Persisted to disk under note_docs.
    from projectum.store import ProjectStore
    reloaded = ProjectStore(window.store.root)
    assert sorted(n.title for n in reloaded.note_docs) == ["Design", "Ideas"]

    # Selecting a note loads it into the editor.
    first = window.store.sorted_notes()[0]
    window.notes_list_widget.setCurrentItem(window._note_items[first.id])
    qapp.processEvents()
    assert window.current_note.id == first.id
    assert window.note_body_edit.toPlainText() == first.body

    # Deleting the current note drops the row and clears selection to the next.
    window._remove_note(first.id); qapp.processEvents()
    assert first.id not in window._note_items
    assert window.notes_list_widget.count() == 1


def test_notes_switch_persists_outgoing_edits(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, "p"))
    window._goto_tab("notes"); qapp.processEvents()
    a = window.store.add_note("A", ""); window._append_note_row(a)
    b = window.store.add_note("B", ""); window._append_note_row(b)
    # Open A and type, then switch to B WITHOUT manually flushing the timer.
    window.notes_list_widget.setCurrentItem(window._note_items[a.id])
    qapp.processEvents()
    window.note_body_edit.setPlainText("unsaved edit to A")
    window.notes_list_widget.setCurrentItem(window._note_items[b.id])
    qapp.processEvents()
    # _on_note_select must save A's edits before loading B.
    assert window.store.get_note(a.id).body == "unsaved edit to A"
    assert window.current_note.id == b.id


def test_notes_tab_migrates_legacy_scratchpad(window, tmp_path, qapp):
    import json
    ws = make_folder(tmp_path, "p")
    (ws / ".projectum.json").write_text(
        json.dumps({"version": 2, "notes": "# Hi\nold body"})
    )
    window.load_folder(ws); qapp.processEvents()
    window._goto_tab("notes"); qapp.processEvents()
    assert window.notes_list_widget.count() == 1
    assert window.current_note is not None
    assert window.note_title_edit.text() == "Hi"   # leading '#' stripped


def _wheel(viewport, dy, pixel=False):
    return QWheelEvent(
        QPointF(10, 10), viewport.mapToGlobal(QPoint(10, 10)),
        QPoint(0, dy) if pixel else QPoint(), QPoint(0, dy),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)


def test_smoothscroll_trackpad_passthrough_and_wheel_consumed(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, *[f"p{i}" for i in range(60)]))
    qapp.processEvents()
    lw = window.list_widget
    sf = next(f for f in window._scroll_filters if getattr(f, "_target", None) is lw)
    vp = lw.viewport()
    # Trackpad (pixelDelta) is left to native handling.
    assert sf.eventFilter(vp, _wheel(vp, -30, pixel=True)) is False
    # A discrete mouse-wheel notch is consumed and starts a glide downward.
    start = lw.verticalScrollBar().value()
    assert sf.eventFilter(vp, _wheel(vp, -120)) is True
    assert sf._timer.isActive() and sf._target_value > start
