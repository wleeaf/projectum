# Changelog

All notable changes to Projectum are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0] — 2026-06-18

### Changed

- **New app icon — an italic serif "P".** The old "W" mark is replaced everywhere by a high-contrast display-serif **P** in the signature purple-to-pink gradient, matching the mark on [projectum.wleeaf.dev](https://projectum.wleeaf.dev). The glyph is baked to a font-independent vector path, so it stays crisp at every size, and one generator (`packaging/make_icon.py`) renders all formats from a single source. The in-app title-bar mark follows the active theme accent.
- **Windows and macOS binaries now carry the real icon.** The PyInstaller `.exe` and `.app` builds were shipping with the default placeholder icon; they now embed the Projectum mark (`.ico` / `.icns`).

[2.2.0]: https://github.com/wleeaf/projectum/releases/tag/v2.2.0

## [2.1.0] — 2026-06-10

### Added

- **Projectum keeps itself up to date.** When a newer release is found at launch, the app now installs it automatically — replacing the AppImage in place, fast-forwarding a git checkout (only when clean and on `main`), or upgrading the pip package — then offers a one-click **Restart** to apply. Frozen Windows/macOS builds keep the manual Download banner, since a running binary can't safely replace itself. The settings toggle ("Keep Projectum up to date automatically") still opts the whole thing out, and a failed install falls back to the old Download banner.

### Fixed

- **Checkboxes are now visible in light themes.** The check indicator was unstyled native Qt, which rendered dark with an unseeable tick on light palettes. It's now themed: a surface-colored box with a border, and an accent fill with a contrast-guaranteed checkmark when ticked.

[2.1.0]: https://github.com/wleeaf/projectum/releases/tag/v2.1.0

## [2.0.0] — 2026-06-10

### Changed

- **Markdown markers now reveal per-phrase, not per-line.** Inline syntax (`**bold**`, `*italic*`, `` `code` ``, `~~strike~~`, `[label](url)`) stays concealed even on the line you're editing — the markers only reappear while the cursor is inside that phrase. Line-level markers (heading `#`s, blockquote `>`) still reveal for the whole line. The result reads much closer to a live preview while you type.
- **Every theme's muted text is readable now.** `TEXT_MUTED` (hints, metadata, helper text) was below the WCAG AA 4.5:1 contrast floor in all 19 themes — as low as 3.0:1 in `dark`. Each palette's muted color was blended minimally toward its text color to clear 4.5:1 while keeping the theme's hue character; `light` and `solarized_light` also got a slightly darker `TEXT_DIM` so the de-emphasis hierarchy stays ordered. The theme test suite now enforces these floors so future palettes can't regress.
- **Inline Markdown code is legible in every theme.** The accent-pink code color washed out against its background in 10 themes (down to 2.0:1 in `light`); it's now contrast-corrected per theme, hue preserved.

### Fixed

- Two stray border radii (update-banner button, link-search results) now match their families, and a confusing variable shadow in the project-list reconciler was cleaned up.

[2.0.0]: https://github.com/wleeaf/projectum/releases/tag/v2.0.0

## [1.9.0] — 2026-06-04

### Added

- **Relate anything to anything, from a themed panel.** Right-click a project, playlist, todo, note, or a calendar day and pick **Relate to ▸** — Project, Playlist, Todo, Note, or Date. Each opens an in-app panel rather than a sprawling submenu: the entity kinds give you a searchable, scrollable list (scales to hundreds of items), and **Date** opens a themed calendar picker built on Projectum's own month grid — no more OS-native date popup. **Links…** stays as the full see-and-remove view.
- **Notes are first-class in the relation graph.** Notes are now linkable like everything else — relate a note to a project, a date, another note, whatever — searchable in the relate panels, with their own relate menu, and their links are pruned when the note is deleted.
- **An inline "Related" view.** The project, playlist, and note detail panels now show a strip of clickable chips for what an item is linked to (color-coded by kind), so you can see and jump to relations without opening a dialog. Bare durations appear but aren't clickable.
- **The "by wleeaf" attribution is now a link** to the author's GitHub (in the title bar and the welcome footer).

### Changed

- The date picker used anywhere you attach a date is now the in-app themed calendar instead of the platform's native control.

[1.9.0]: https://github.com/wleeaf/projectum/releases/tag/v1.9.0

## [1.8.0] — 2026-06-04

### Added

- **Relations — connect anything to anything.** Right-click a project, playlist, or todo and pick **Links…** to attach it to another entity, to a calendar day, to a span of days (a date frame), or to a bare duration like "2 weeks". Links are undirected and untyped — open the other side later and the first shows up as a backlink. They're stored globally (`~/.config/projectum/links.json`), so they reach across every folder you've opened. The add box is a focused search: type and press Enter to attach the top match.
- **Calendar tab.** A month view that's a browser over those relations — anything linked to a date appears on that day, and date frames draw as bars that span and wrap across weeks. Hover a day to highlight it, click it to see and attribute what's on it, or drag across days to make a frame. Global across folders, color-coded by kind (projects / playlists / todos), and rendered off the UI thread.
- **Notes is now a notebook.** The single folder-wide scratchpad became a list of named notes — create, rename, search, reorder, delete — beside an editor with a title field. Your old scratchpad migrates into a first note automatically.
- **Cursor-aware Markdown.** Every notes pane now hides the syntax markers (`#`, `**`, backticks, link brackets, `>`) on every line except the one the cursor is on, where they reappear so you can edit them — an in-place live preview. The document underneath stays plain, raw Markdown.
- **Suspended and Failed project states.** Two toggles beside Done/Tested, each recoloring the project so on-hold and abandoned work reads at a glance.
- **GitHub Sponsors.** An optional **Sponsor** link; Projectum stays free, with no servers, accounts, or telemetry.

### Fixed

- **Negative size for folders over 2 GB.** The size probe carried bytes through a 32-bit signal and wrapped large folders to a negative number; it's a 64-bit value now.

### Removed

- **The experimental Graph view.** The Calendar and the Links dialog cover the same ground more directly, so the separate radial graph was retired.

[1.8.0]: https://github.com/wleeaf/projectum/releases/tag/v1.8.0

## [1.7.0] — 2026-06-02

### Added

- **In-app update check.** Shortly after launch, Projectum makes a single read-only call to the GitHub Releases API; if a newer version is out, a quiet banner ("Projectum vX.Y.Z is available" + Download) appears. Off the UI thread, fail-silent when offline, **no telemetry**, opt-out via a new **Check for updates on launch** toggle in Settings, and a dismissed version won't nag again.
- **`pip install projectum`** — the app is now published to PyPI (auto-published from each release).

[1.7.0]: https://github.com/wleeaf/projectum/releases/tag/v1.7.0

## [1.6.0] — 2026-06-02

### Added

- **5 distinctive themes** to break up a pack that had drifted toward look-alike dark/cool palettes — **Midnight** (true-black OLED, electric teal), **Synthwave** (deep indigo, neon magenta/cyan), **Ember** (warm near-black, orange-red), **Graphite** (colorless charcoal + silver), and **Paper** (crisp white, emerald). **19 themes** total, spread across the color space. Each clears the readability gate and is previewed by a swatch in the theme picker.

[1.6.0]: https://github.com/wleeaf/projectum/releases/tag/v1.6.0

## [1.5.0] — 2026-06-02

### Changed

- **Settings is now dropdown-driven with an Apply button.** Font family is a select-only picker (each family previewed in its own font) and font size is a preset-pixel dropdown — no more typing either. The theme dropdown previews each palette with a color swatch (background + accent). Changes no longer apply live; they're staged and applied when you click **Apply** (enabled only when something changed), and Close/Esc dismisses without applying.

[1.5.0]: https://github.com/wleeaf/projectum/releases/tag/v1.5.0

## [1.4.0] — 2026-06-02

### Added

- **Five new themes** — One Dark, GitHub Dark, Catppuccin Macchiato, Everforest Dark, and Gruvbox Light — bringing the total to **14**.
- **Smooth theme transitions** — switching themes now crossfades the old appearance into the new one instead of snapping.
- **A theme readability gate** — every theme is checked in CI against per-role WCAG contrast floors (body text ≥ 4.5; muted/semantic/accent text ≥ 3.0), so new themes can't ship unreadable.

### Changed / Fixed

- **Tag chips are now legible on every theme.** They previously painted the fixed pastel palette colors as text, which was unreadable on light backgrounds; the chip text/border now uses a contrast-adjusted "ink" (hue preserved) derived from the tag color and the active theme.
- **Readability fixes across themes** — corrected 15 sub-floor colors (muted section labels and semantic status/git/error text on light themes; error text on Nord and Solarized Dark) so all text clears the contrast gate.

[1.4.0]: https://github.com/wleeaf/projectum/releases/tag/v1.4.0

## [1.3.0] — 2026-05-30

### Added

- **Project quick-actions** — right-click a project for **Open folder**, **Copy path**, **Open in terminal**, and **Open in your editor** (VS Code, Cursor, Zed, or Sublime, when one is on `PATH`).
- **Git status** in the project detail panel — current branch plus a dirty/clean indicator, read off the UI thread in a single `git status` call.
- **Recent-folders menu** — a **Recent ▾** button in the top bar to jump between tracked folders (most-recent-first, deduplicated, with missing paths filtered out).
- **Keyboard shortcuts** — `Ctrl+1`–`Ctrl+4` switch tabs, `Ctrl+D` toggles the selected project's *done* state, `Ctrl+T` jumps to the Todo tab and focuses a new task.
- **Test suite** — a headless `pytest` suite (data-layer round-trips, the 1.1.0/1.2.0 regressions, the todo lifecycle, and smooth-scroll behavior) now runs in CI across Linux/macOS/Windows × Python 3.10–3.12.

### Changed

- **Mouse-wheel scrolling** now ticks at the display's actual refresh rate (read from the shown window, with a `PreciseTimer`) instead of a fixed ~60 fps, so the wheel glide is as smooth as dragging the scrollbar on high-refresh panels. Set `PROJECTUM_SCROLL_FPS` to override if your setup misreports its refresh rate.

[1.3.0]: https://github.com/wleeaf/projectum/releases/tag/v1.3.0

## [1.2.0] — 2026-05-30

### Added

- **Todo tab** — a folder-scoped to-do list beside Projects, Playlists, and Notes. Add tasks (Enter), check them off, double-click to edit inline, delete, and drag to reorder; a done/total counter and an empty state round it out. Tasks persist in the folder's `.projectum.json` and are searchable from the `Ctrl+K` command palette.

### Changed

- **Smooth mouse-wheel scrolling** rebuilt as a single continuous glide toward an accumulating target, eased with frame-rate-independent damping, replacing the per-notch ease-out that decelerated from a standstill on every notch and stuttered on fast spins. Trackpad / high-precision scrolling continues to use the native low-latency path.

[1.2.0]: https://github.com/wleeaf/projectum/releases/tag/v1.2.0

## [1.1.0] — 2026-05-29

A large correctness, performance, and distribution release. Adds a self‑contained Linux AppImage, replaces the Markdown preview toggle with a live WYSIWYG highlighter, and fixes a broad set of data‑loss, crash, and smoothness issues found in an exhaustive review.

### Added

- **Linux AppImage** — a self‑contained, single‑file build (bundles Python, Qt, PySide6, `yt-dlp`) plus a tagged GitHub release pipeline that builds and publishes it automatically.
- **WYSIWYG Markdown** in every notes pane — headings, bold/italic, inline & fenced code, lists, blockquotes, strikethrough and links render live as you type; the old editor⇄preview toggle is gone.
- **Smooth wheel scrolling** for mouse wheels, with high‑precision/trackpad input passed through to the native (lower‑latency) path.

### Fixed

- **Playlist *Refresh* could corrupt the wrong playlist.** If you switched playlists while a refresh was in flight, the fetched data merged into the newly‑selected playlist. Refreshes now resolve their target by URL.
- **Drag‑reordering could blank rows and crash.** Qt's internal move dropped the row widget and left stale item pointers, which could raise `RuntimeError` on the next filter/search; rows are now re‑attached correctly.
- **Pinned state and manual order were lost** when a project folder disappeared and later returned (the orphan store ignored `pinned`/`position`).
- **Opening a folder wiped persisted settings** — theme, font, and window geometry were clobbered because the last‑folder write replaced the whole state file.
- **Tag row "squeeze."** Removing one of two tags crushed the sidebar row to the no‑tags height; row heights now settle correctly after a tag change.
- **Mid‑edit notes could be lost** when a filter or search hid the selected project before the debounced save fired.
- **Duplicate video IDs** in a playlist collapsed onto one object on reload/refresh, losing per‑occurrence watched state and notes.
- **Filter and search leaked across folders** when opening a new folder.
- **Corrupt or hand‑edited `state.json` / `.projectum.json`** could crash startup or make a folder unopenable; parsing is now defensive and type‑guarded.
- **Stuck buttons** — *Refreshing…* / *Fetching…* no longer get stuck after a folder change mid‑operation.
- **Changing a tag's color** no longer closes the open tag editor or discards typed input.
- **Markdown rendering** — inline code is no longer overwritten by bold/italic when nested, and inline markup inside headings keeps the heading size.
- Several Qt object leaks (color‑picker popup, command palette, settings dialog, per‑call animations) and assorted type‑guard crashes on unexpected persisted values.

### Performance

- Drag‑reorder reconciles in milliseconds instead of rebuilding the entire list (which froze for ~½ s on large folders).
- Search filtering is debounced, so typing stays responsive on big folders.
- Shorter, snappier transition animations.

### Engineering

- Continuous integration boots `MainWindow` on a headless `offscreen` display and lints with `ruff`; the release is verified end‑to‑end before publishing.

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

[1.1.0]: https://github.com/wleeaf/projectum/releases/tag/v1.1.0
[1.0.0]: https://github.com/wleeaf/projectum/releases/tag/v1.0.0
