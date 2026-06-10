"""Regenerate the README / website screenshots, offscreen.

Builds a believable demo workspace in a temp dir, boots the real MainWindow
on the offscreen platform, and grabs each tab in a chosen theme:

    .venv/bin/python docs/make_screenshots.py

Output goes to docs/screenshots/. No display needed, nothing touches your
real config (XDG_CONFIG_HOME is redirected, update check disabled).
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
_tmp = tempfile.mkdtemp(prefix="projectum-shots-")
os.environ["XDG_CONFIG_HOME"] = str(Path(_tmp) / "config")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from projectum import theme  # noqa: E402
from projectum.links import date_ref, daterange_ref, make_ref  # noqa: E402
from projectum.store import Video  # noqa: E402

OUT = Path(__file__).resolve().parent / "screenshots"
SIZE = (1280, 800)

DEMO_PROJECTS = [
    # (name, tags, completed, tested, pinned, notes, start, end)
    ("aurora-engine", ["rust", "core"], False, False, True,
     "# Aurora\n\nRendering core. Targeting **Vulkan 1.3** first, metal later.\n\n- [x] swapchain rebuild on resize\n- [ ] bindless textures\n- [ ] `frame-graph` pass culling", "2026-06-08", "2026-06-19"),
    ("web-client", ["ui", "typescript"], False, False, False,
     "SPA talking to `auth-service`. **Storybook** covers the design system.", "2026-06-15", "2026-06-26"),
    ("auth-service", ["api", "go"], True, True, False,
     "Token issuing + refresh rotation. *Done — audited 2026-05.*", "", ""),
    ("dotfiles", ["config"], False, False, False, "", "", ""),
    ("ml-notebooks", ["python", "research"], False, False, False,
     "Experiments for the embeddings post.", "2026-06-22", "2026-06-24"),
    ("legacy-importer", ["python"], True, False, False, "", "", ""),
]

DEMO_TODOS = [
    ("Write the v2 migration guide", False, "2026-06-12", ""),
    ("Benchmark the new renderer against 1.9", False, "2026-06-16", "2026-06-18"),
    ("Rotate the staging TLS certs", True, "", ""),
    ("Review aurora-engine frame-graph PR", False, "", ""),
]

DEMO_NOTES = [
    ("Release checklist", "# Release checklist\n\n- [x] changelog entry\n- [x] bump version in **both** places\n- [ ] wait for CI, then `gh release create`\n- [ ] verify the three assets attach\n\nSee the [releasing doc](https://example.com) for the long form."),
    ("Vulkan sync notes", "# Vulkan sync notes\n\nBarriers are about *visibility*, not ordering. The `srcStageMask` says **what to wait for**, the dst side says **who waits**.\n\n`VK_PIPELINE_STAGE_2_COPY_BIT` covers transfer."),
    ("Reading queue", "# Reading queue\n\n- ~~Designing Data-Intensive Applications~~ (done)\n- A Philosophy of Software Design\n- The `io_uring` paper"),
]

DEMO_PLAYLIST = {
    "id": "PLdemo",
    "title": "Rust for Rustaceans — study group",
    "uploader": "Jon Gjengset",
    "videos": [
        ("Lifetimes, deeply", 3120, True),
        ("Dispatch and fat pointers", 2895, True),
        ("Declarative macros", 3410, True),
        ("Async: pinning explained", 4150, False),
        ("Unsafe, soundly", 3680, False),
        ("FFI without tears", 2710, False),
    ],
}

SHOTS = [
    # (filename, theme, tab, selected_project)
    ("01-projects-dark.png", "dark", "projects", "aurora-engine"),
    ("02-calendar-midnight.png", "midnight", "calendar", None),
    ("03-notes-paper.png", "paper", "notes", None),
    ("04-playlists-light.png", "light", "playlists", None),
    ("05-todo-gruvbox.png", "gruvbox", "todos", None),
]


def build_workspace() -> Path:
    root = Path(_tmp) / "code"
    for name, *_ in DEMO_PROJECTS:
        d = root / name
        (d / "src").mkdir(parents=True, exist_ok=True)
        (d / "src" / "main.txt").write_text("demo\n" * 200)
    return root


def populate(win) -> None:
    store = win.store
    for name, tags, done, tested, pinned, notes, start, end in DEMO_PROJECTS:
        p = store.projects.get(name)
        if p is None:
            continue
        p.tags, p.completed, p.tested, p.pinned = tags, done, tested, pinned
        p.notes, p.start, p.end = notes, start, end
    for text, done, start, end in DEMO_TODOS:
        t = store.add_todo(text)
        t.done, t.start, t.end = done, start, end
    for title, body in DEMO_NOTES:
        store.add_note(title=title, body=body)
    pl = store.add_playlist(
        "https://www.youtube.com/playlist?list=PLdemo",
        {"id": DEMO_PLAYLIST["id"], "title": DEMO_PLAYLIST["title"],
         "uploader": DEMO_PLAYLIST["uploader"], "videos": []},
    )
    pl.videos = [
        Video(id=f"v{i}", title=t, url=f"https://youtu.be/v{i}",
              duration=dur, completed=done)
        for i, (t, dur, done) in enumerate(DEMO_PLAYLIST["videos"])
    ]
    pl.tags = ["rust", "learning"]
    store.save()

    # The calendar renders the link graph: connect the scheduled things to
    # their dates/frames the same way the UI's "Relate to Date" would.
    root = str(store.root)
    links = win._link_store
    for name, *rest in DEMO_PROJECTS:
        start, end = rest[-2], rest[-1]
        if not start:
            continue
        temporal = daterange_ref(start, end) if end and end != start else date_ref(start)
        links.add(make_ref("project", root, name), temporal)
    for todo in store.todos:
        if todo.start:
            temporal = (daterange_ref(todo.start, todo.end)
                        if todo.end and todo.end != todo.start else date_ref(todo.start))
            links.add(make_ref("todo", root, todo.id), temporal)
    links.add(make_ref("playlist", root, pl.id), daterange_ref("2026-06-02", "2026-06-13"))
    links.save()


def switch_tab(win, key: str) -> None:
    for b in win._tab_group.buttons():
        if b.property("tab_key") == key:
            b.setChecked(True)
            b.clicked.emit()
            return


def main() -> None:
    app = QApplication([])
    # Keep the auto-updater out of the screenshots (and away from this repo).
    from projectum.app import MainWindow, save_state
    save_state({"settings": {"check_updates": False}})

    theme.apply_theme("dark")
    app.setStyleSheet(theme.build_stylesheet())
    win = MainWindow()
    win.resize(*SIZE)
    win.show()
    app.processEvents()

    win.load_folder(build_workspace())
    app.processEvents()
    populate(win)
    win.refresh()
    win._update_stats()
    app.processEvents()

    OUT.mkdir(parents=True, exist_ok=True)
    for fname, theme_name, tab, project in SHOTS:
        win._apply_settings_now(
            theme_name, theme.current_font_family(), theme.DEFAULT_FONT_SIZE,
            check_updates=False,
        )
        app.processEvents()
        switch_tab(win, tab)
        if tab == "calendar":
            win.calendar_view.set_month(2026, 6)
        if project and project in win._row_items:
            win.list_widget.setCurrentItem(win._row_items[project])
        if tab == "notes" and win._note_items:
            first = win.store.note_docs[0].id
            if first in win._note_items:
                win.notes_list_widget.setCurrentItem(win._note_items[first])
        if tab == "playlists" and win._playlist_items:
            pid = next(iter(win._playlist_items))
            win.playlists_list_widget.setCurrentItem(win._playlist_items[pid])
        # Let crossfades (real-time animations) and deferred loads settle.
        import time
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        pix = win.grab()
        pix.save(str(OUT / fname))
        print(f"{fname}  {pix.width()}x{pix.height()}  ({theme_name}/{tab})")

    win.close()
    app.processEvents()


if __name__ == "__main__":
    main()
