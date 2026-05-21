# Projectum

[![CI](https://github.com/ts-solidarity/projectum/actions/workflows/ci.yml/badge.svg)](https://github.com/ts-solidarity/projectum/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/ts-solidarity/projectum?sort=semver)](https://github.com/ts-solidarity/projectum/releases)

A keyboard-first desktop app for tracking every project, playlist and idea that lives in a folder on your disk — built with PySide6, no servers, no accounts, no telemetry.

Open a folder. Every subfolder becomes a project you can mark done/tested, tag, write Markdown notes against, and pin to the top. The same window tracks YouTube playlists (titles, durations, watched state, per-video notes) and gives you a global scratchpad. `Ctrl+K` opens a fuzzy command palette over everything.

![Projects view with detail panel](docs/screenshots/01-projects.png)

## Highlights

- **Filesystem-backed.** Point at any folder, every subfolder is a project. State lives in a single `.projectum.json` per folder — Git-friendly, sync-friendly, no database.
- **YouTube playlists with per-video tracking.** Paste a URL, `yt-dlp` fetches the metadata, mark videos done as you watch, write per-video notes.
- **Three kinds of notes, all Markdown.** Per-project, per-playlist, per-video, plus a global scratchpad tab — each with a live Edit ↔ Preview toggle.
- **Tags with custom colors** that auto-clean when nothing references them anymore.
- **Pin & drag-to-reorder** projects and playlists; pinned items always float to the top.
- **Done + Tested toggles** on projects (green check, blue check). Tested projects render in blue on the sidebar.
- **Command palette (`Ctrl+K`)** searches projects, playlists, videos, tags, and the scratchpad — type-ahead with prefix-match ranking.
- **9 themes** (Catppuccin Mocha/Latte, Nord, Dracula, Tokyo Night, Rosé Pine, Gruvbox, Solarized Dark/Light) and **any installed font** at any pixel size — switch live from a clean settings panel.
- **Frameless, animated UI.** Pixmap-snapshot crossfades for big transitions, geometry slides for sections — no `QGraphicsOpacityEffect` flicker on widget trees with custom paints.

## Screenshots

|   |   |
|---|---|
| **Projects** &nbsp;·&nbsp; tagged, pinned, tested<br>![Projects](docs/screenshots/01-projects.png) | **Playlists** &nbsp;·&nbsp; videos, watched count, per-playlist notes<br>![Playlists](docs/screenshots/02-playlists.png) |
| **Notes** &nbsp;·&nbsp; folder-level scratchpad with Markdown preview<br>![Notes](docs/screenshots/03-notes-preview.png) | **Command palette** &nbsp;·&nbsp; `Ctrl+K` over everything<br>![Palette](docs/screenshots/04-command-palette.png) |
| **Settings** &nbsp;·&nbsp; theme, font family, font size<br>![Settings](docs/screenshots/05-settings.png) | **Light theme** &nbsp;·&nbsp; same app, different palette<br>![Light](docs/screenshots/06-light-theme.png) |

## Install

Requires **Python ≥ 3.10**. PySide6 wheels exist for Linux, macOS and Windows.

```bash
git clone https://github.com/ts-solidarity/projectum.git
cd projectum
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Or pass a folder directly:

```bash
python main.py ~/code
```

Projectum remembers the last folder you opened, so subsequent launches go straight to it.

## Usage

### Projects tab

- Each subfolder of the chosen root becomes a row. Mark a project **done** with the green check or **tested** with the blue one. Tested projects show their name in blue.
- Tags are inline chips. Right-click a chip to recolor it; click the **×** to remove. Filter by tag from the **Tag** chip at the top of the sidebar.
- Drag rows to reorder; right-click a row → **Pin to top** to make a project sticky.
- Notes support full Markdown; the **Preview** button toggles a rendered view.

### Playlists tab

- **+ Add YouTube playlist** prompts for a URL. `yt-dlp` fetches title, uploader, and every video (titles + durations). Refresh later to pull in new uploads — your watched/notes state is preserved.
- Tag, pin, reorder, and write notes per playlist; each video has its own notes pane below the video list.

### Notes tab

A single, folder-wide scratchpad. Has Markdown preview and a search bar with `↵` / `Shift+↵` to jump between matches.

### Command palette

`Ctrl+K` from anywhere. Type to filter projects, playlists, videos, tags, and the scratchpad. `↑` / `↓` to navigate, `↵` to open, `Esc` to dismiss.

### Settings

Gear icon in the top bar (or any time you want a different look). Theme, font family (any installed family — type to filter), and font size (9–28 px) — applied immediately and persisted.

## Keyboard shortcuts

| Shortcut          | Action                                         |
|-------------------|------------------------------------------------|
| `Ctrl+K`          | Open command palette                           |
| `Ctrl+O`          | Open a folder                                  |
| `Ctrl+F`          | Focus the sidebar search                       |
| `Ctrl+N`          | Focus the project notes editor                 |
| `Ctrl+R`          | Refresh the current folder                     |
| `↵` / `Shift+↵`   | Next / previous match in Notes search          |
| `Esc`             | Close a popup (color picker, settings, palette) |

## Storage

All state for a folder lives in `<folder>/.projectum.json` — one JSON file with projects, playlists, tags, notes, pins, and positions. Commit it to Git alongside the rest of your work, or `.gitignore` it. Nothing else is written outside the folder, except `~/.config/projectum/state.json` which remembers your window geometry, last opened folder, and theme/font settings.

When a project folder disappears (rename, `git checkout`), its metadata is preserved in an `_orphans` bucket so it comes back intact if the folder reappears.

## Project layout

```
projectum/
├── main.py                  # entry point
├── projectum/
│   ├── __init__.py          # version
│   ├── app.py               # MainWindow + run()
│   ├── store.py             # Project / Playlist / Video / ProjectStore
│   ├── widgets.py           # custom-painted widgets (chips, toggles, palette, …)
│   ├── theme.py             # 9 themes + stylesheet builder + apply_theme/apply_font
│   ├── anims.py             # crossfade / slide / progress helpers
│   ├── youtube.py           # yt-dlp runnable
│   └── assets/icon.svg
├── requirements.txt
├── LICENSE                  # MIT
└── docs/screenshots/
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The codebase is intentionally dependency-light: `PySide6` for the UI, `yt-dlp` for playlist metadata, and the standard library for the rest. No async, no ORM, no test framework — there's a smoke-import check in CI and the rest you exercise by running the app.

## License

[MIT](LICENSE) — © 2026 wleeaf.
