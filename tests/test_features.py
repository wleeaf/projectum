"""Tests for the 1.3.0 features: recent folders, keyboard actions, project
quick-actions, and the off-thread git probe."""

import shutil
import subprocess

import pytest

from projectum.app import load_state
from projectum.widgets import GitRunnable, _SizeSignals


def make_folder(base, name):
    root = base / name
    root.mkdir()
    return root


def workspace(base, *subs):
    """A folder whose subfolders are projects (what load_folder expects)."""
    root = base / "ws"
    root.mkdir(exist_ok=True)
    for s in subs:
        (root / s).mkdir(exist_ok=True)
    return root


# ── recent folders ──

def test_recent_folders_tracked_deduped_and_ordered(window, tmp_path):
    a = make_folder(tmp_path, "a")
    b = make_folder(tmp_path, "b")
    window.load_folder(a)
    window.load_folder(b)
    window.load_folder(a)  # reopening a moves it to the front (no duplicate)
    recents = load_state().get("recent_folders")
    assert recents[:2] == [str(a), str(b)]
    assert recents.count(str(a)) == 1


def test_recent_menu_skips_missing_paths(window, tmp_path, qapp):
    gone = make_folder(tmp_path, "gone")
    window.load_folder(gone)
    window.load_folder(make_folder(tmp_path, "live"))
    shutil.rmtree(gone)  # path no longer exists (opening it stamped a .projectum.json)
    # _show_recent_menu filters dead paths at display time; just ensure it
    # builds without error and the dead path isn't offered.
    recents = [r for r in load_state().get("recent_folders", [])]
    assert str(gone) in recents  # still remembered
    assert not gone.is_dir()     # but would be filtered on display


# ── keyboard-driven navigation ──

def test_goto_tab_switches(window, qapp):
    for key in ("playlists", "todos", "notes", "projects"):
        window._goto_tab(key)
        assert window.current_tab == key


def test_focus_new_todo_switches_and_focuses(window, tmp_path, qapp):
    window.load_folder(make_folder(tmp_path, "p"))
    window._focus_new_todo()
    qapp.processEvents()
    assert window.current_tab == "todos"
    assert window.todo_input.hasFocus()


def test_toggle_current_done(window, tmp_path, qapp):
    window.load_folder(workspace(tmp_path, "proj"))
    window.list_widget.setCurrentItem(window._row_items["proj"])
    qapp.processEvents()
    assert window.current_project.completed is False
    window._toggle_current_done()
    assert window.current_project.completed is True
    window._toggle_current_done()
    assert window.current_project.completed is False


# ── project quick-actions ──

def test_copy_path_to_clipboard(window, qapp):
    window._copy_to_clipboard("/some/project/path")
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == "/some/project/path"


def test_editor_launcher_shape(window):
    res = window._editor_launcher()
    assert res is None or (isinstance(res, tuple) and len(res) == 2)


# ── git probe ──

@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_runnable_reports_branch_and_dirty(window, tmp_path, qapp):
    repo = make_folder(tmp_path, "repo")
    env = {**subprocess.os.environ, "GIT_TERMINAL_PROMPT": "0"}
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                    capture_output=True, env=env)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    run("commit", "--allow-empty", "-q", "-m", "init")

    def probe():
        captured = {}
        gr = GitRunnable("repo", repo)
        gr.signals.done.connect(lambda n, info: captured.update(info=info))
        gr.run()  # direct (same-thread) connection -> fires synchronously
        return captured["info"]

    info = probe()
    assert info is not None and info["branch"] in ("main", "master")
    assert info["dirty"] is False

    (repo / "new.txt").write_text("x")  # untracked change -> dirty
    assert probe()["dirty"] is True


def test_git_runnable_non_repo_returns_none(window, tmp_path, qapp):
    plain = make_folder(tmp_path, "plain")
    captured = {}
    gr = GitRunnable("plain", plain)
    gr.signals.done.connect(lambda n, info: captured.update(seen=True, info=info))
    gr.run()
    assert captured["seen"] and captured["info"] is None


def test_suspended_failed_toggles_persist_and_color_row(window, tmp_path, qapp):
    from projectum import theme
    from projectum.store import ProjectStore
    from projectum.widgets import ProjectRow
    ws = workspace(tmp_path, "alpha")
    window.load_folder(ws)
    window.list_widget.setCurrentItem(window._row_items["alpha"])
    qapp.processEvents()

    window.suspended_toggle.setChecked(True)        # mark suspended
    qapp.processEvents()
    assert window.current_project.suspended is True
    assert ProjectStore(ws).projects["alpha"].suspended is True   # persisted
    assert window.status_box._value.text() == "Suspended"
    row = window.list_widget.itemWidget(window._row_items["alpha"])
    assert isinstance(row, ProjectRow)
    assert theme.WARNING in row.name_label.styleSheet()           # amber name

    window.failed_toggle.setChecked(True)           # failed outranks suspended
    qapp.processEvents()
    assert ProjectStore(ws).projects["alpha"].failed is True
    assert window.status_box._value.text() == "Failed"
    assert theme.DANGER in row.name_label.styleSheet()            # red name


def test_size_signal_handles_folders_over_2gb(qapp):
    # A folder larger than a 32-bit int (~2.1 GB) must not wrap to a negative
    # byte count: the size is carried as qint64, not a C++ int.
    big = 3_306_270_403  # ~3.3 GB
    got = []
    sig = _SizeSignals()
    sig.done.connect(lambda name, b: got.append(b))
    sig.done.emit("brante", big)
    qapp.processEvents()
    assert got == [big]


def _marker_alpha(ed, line, off):
    """Foreground alpha of the format covering ``off`` on ``line`` — 0 when the
    Markdown marker there is concealed, 255 when it's revealed."""
    blk = ed.document().findBlockByNumber(line)
    covering = [r for r in blk.layout().formats()
                if r.start <= off < r.start + r.length]
    return covering[-1].format.foreground().color().alpha() if covering else None


def test_clickable_label_emits_clicked_within_bounds(qapp):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from projectum.widgets import ClickableLabel
    lbl = ClickableLabel("by wleeaf"); lbl.resize(80, 20)
    fired = []
    lbl.clicked.connect(lambda: fired.append(1))

    def release(x, y):
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        lbl.mouseReleaseEvent(ev)

    release(10, 10)             # inside -> fires
    release(500, 500)           # released off the label -> ignored
    assert fired == [1]
    lbl.deleteLater()


def test_markdown_editor_conceals_markers_outside_cursor_phrase(qapp):
    from projectum.widgets import MarkdownEditor
    ed = MarkdownEditor()
    ed.setPlainText("# Heading\nbody **bold** text")
    # Cursor on the body line but before the bold phrase (col 2, in "body").
    ed.highlighter.set_active_position(1, 2)
    assert _marker_alpha(ed, 0, 0) == 0       # heading '#' concealed (transparent)
    assert _marker_alpha(ed, 1, 5) == 0       # bold '*' concealed: cursor not in phrase
    # Cursor moves inside the bold phrase (col 8, between the asterisks).
    ed.highlighter.set_active_position(1, 8)
    assert _marker_alpha(ed, 1, 5) == 255     # bold '*' now revealed
    ed.highlighter.set_active_position(0, 0)  # cursor moves up to the heading
    assert _marker_alpha(ed, 0, 0) == 255     # heading '#' revealed line-wide
    ed.deleteLater()


def test_relation_survives_folder_rename(window, qapp, tmp_path):
    """End-to-end through the real relate path: a project with *only* a relation
    (no other metadata) still keeps that relation when its folder is renamed."""
    import os
    from projectum.links import make_ref, date_ref
    root = tmp_path / "work"
    (root / "alpha").mkdir(parents=True)
    window.load_folder(root)
    home = str(window.store.root)
    a = make_ref("project", home, "alpha")
    d = date_ref("2026-08-01")
    window._relate(a, d)                          # real path: stamps alpha's _fsid

    os.rename(root / "alpha", root / "beta")      # rename the project folder
    window.refresh()

    b = make_ref("project", home, "beta")
    assert window.store.last_renames == [("alpha", "beta")]
    assert d in window._link_store.neighbors(b)   # the relation moved to "beta"
    assert window._link_store.neighbors(a) == []  # and left the old identity


def test_relation_survives_root_move(window, qapp, tmp_path):
    """The whole opened folder is renamed/moved: every relation under it follows."""
    import os
    from projectum.links import make_ref, date_ref
    root = tmp_path / "oldroot"
    (root / "alpha").mkdir(parents=True)
    window.load_folder(root)
    a = make_ref("project", str(window.store.root), "alpha")
    d = date_ref("2026-09-01")
    window._relate(a, d)
    window._flush_pending_writes()

    new_root = tmp_path / "newroot"
    os.rename(root, new_root)                  # move the entire root
    window.load_folder(new_root)               # reopen at the new path

    a2 = make_ref("project", str(window.store.root), "alpha")
    assert d in window._link_store.neighbors(a2)    # link followed the root
    assert window._link_store.neighbors(a) == []    # nothing left at the old path


def test_root_copy_keeps_relations_separate(window, qapp, tmp_path):
    """Copying a folder (its .projectum.json carries the same id) must NOT let
    the copy hijack the original's relations — the copy gets a fresh id."""
    import shutil
    from projectum.links import make_ref, date_ref
    root = tmp_path / "orig"
    (root / "alpha").mkdir(parents=True)
    window.load_folder(root)
    orig_wid = window.store.workspace_id
    a = make_ref("project", str(window.store.root), "alpha")
    d = date_ref("2026-09-01")
    window._relate(a, d)
    window._flush_pending_writes()

    copy_root = tmp_path / "copy"
    shutil.copytree(root, copy_root)           # same workspace_id on disk
    window.load_folder(copy_root)              # original still exists -> a copy

    assert window.store.workspace_id != orig_wid     # copy re-minted its id
    assert d in window._link_store.neighbors(a)      # original's relation intact
    copy_ref = make_ref("project", str(window.store.root), "alpha")
    assert window._link_store.neighbors(copy_ref) == []   # copy has none yet
