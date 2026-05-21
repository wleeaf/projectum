# Changelog

All notable changes to Projectum are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-05-21

The first stable release.

### Highlights

- **Three workspaces** in one window — Projects (filesystem-backed), Playlists (YouTube via `yt-dlp`), Notes (folder-wide scratchpad).
- **Markdown everywhere.** Per-project, per-playlist, per-video and global notes all toggle between an editor and a live preview.
- **Pin & drag-to-reorder** for projects and playlists.
- **Tested toggle** (blue) alongside the Done toggle (green); tested projects show their name in blue on the sidebar.
- **`Ctrl+K` command palette** over projects, playlists, videos, tags and the scratchpad — prefix-ranked, type-ahead.
- **9 themes** (Catppuccin Mocha/Latte, Nord, Dracula, Tokyo Night, Rosé Pine, Gruvbox Dark, Solarized Dark/Light), **any installed font family**, free-form font size — live via the settings panel.
- **Animation system overhaul.** Pixmap-snapshot crossfades replace the QGraphicsOpacityEffect path that visibly glitched on widget trees with custom paints; geometry slides for collapsible sections.
- **Per-folder state** persisted in a single `.projectum.json` — Git-friendly. Window/theme prefs live in `~/.config/projectum/state.json`.
- **Frameless window** with custom title bar, drag-to-move, resize handles on all edges.
- **Tag system** with custom colors per tag (right-click any chip), automatic cleanup when nothing references a tag, and a tag filter on the sidebar.
- **Video tracking** — per-video done state, per-video notes, watched count, animated progress bar.

### Engineering notes

- Instances of `Project`, `Playlist`, `Video` are now preserved across `store.load()` so callers holding references stay valid across the file-watcher debounce.
- `QApplication.setFont` + per-window broadcast on settings change, so font family selection actually applies (was previously silently overridden by QSS).
- Custom-painted widgets read `theme.X` lazily so theme changes don't require widget rebuilds for repaints.

[1.0.0]: https://github.com/ts-solidarity/projectum/releases/tag/v1.0.0
