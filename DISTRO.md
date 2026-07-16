# Distribution status

State of every channel Projectum ships through, and what's left to do. Last
updated **2026-06-18**, against release **v2.4.0**.

> The in-repo `packaging/` files are the source of truth. The Homebrew/Scoop
> tap repos are live deployment copies. See also `packaging/README.md`.

## Pick up here next visit

- [x] **AUR** — **LIVE** at [aur.archlinux.org/packages/projectum](https://aur.archlinux.org/packages/projectum), `yay -S projectum` (published 2.4.0-1 by `wleeaf`; build-verified, namcap clean). Each new release: bump + re-push (steps below).
- [x] **Flathub** — **not pursuing (declined).** The manifest is build-verified (6.10, builds + launches) and lint-clean, and stays in `packaging/flatpak/` as reference. But Flathub's [requirements](https://docs.flathub.org/docs/for-app-authors/requirements) forbid both AI-opened PRs *and* apps with "AI-generated or AI-assisted code" ("rejected without any further review"), which Projectum can't honestly clear. Linux is already covered by AppImage + AUR + PyPI, so nothing's lost.
- [ ] **Automate per-release bumps** — optional: wire Homebrew/AUR/Flathub checksum+version bumps into the release workflow so they don't drift (offered, not yet done).
- [ ] **Signing** (unblocks store warnings + winget/Microsoft Store/Mac App Store) — Apple Developer membership ($99/yr) + a Windows cert (Azure Trusted Signing is ~free for individuals).
- [ ] **Future channels not started** — winget, conda-forge, Snap, Fedora COPR / openSUSE OBS, nixpkgs.

## Foundation: the updater guard (v2.3.0)

The channels all leave updates to the package manager. v2.3.0 added a `managed`
install channel to the auto-updater (`projectum/update.py`): Flatpak
(`FLATPAK_ID`/`/.flatpak-info`), Snap (`SNAP`), conda (a `conda-meta` record),
and PEP 668 externally-managed distro/Homebrew Python are detected and **never**
self-update via pip — they get an info-only banner instead. AppImage, source
checkouts, and writable pip envs still update in place.

### v2.4.0 artifact checksums (sha256)

| Artifact | URL | sha256 |
|---|---|---|
| Source tarball | `…/archive/refs/tags/v2.4.0.tar.gz` | `1554e5b4369cca7c10e47f9b7b7cd662647f1dda06d7fbd2c76f9f3c5d411788` |
| Windows `.exe` | `…/releases/download/v2.4.0/Projectum-windows-x64.exe` | `2560622ab76b7698081a7ebd85ee2f2eca566f9335ef2e56a66d474c50878d9d` |
| macOS `.dmg` | `…/releases/download/v2.4.0/Projectum-macos.dmg` | `d536a92b5517dde122dd94e7d3969f259aae6d95b2c1c710a387f7c1b0e6df33` |

(Base URL: `https://github.com/wleeaf/projectum`.)

## Channel status

| Channel | Install | Where it lives | Status |
|---|---|---|---|
| **PyPI** | `pip install projectum` | `publish-pypi.yml` | Live |
| **AppImage** (Linux) | download from releases | `packaging/appimage/` + `release.yml` | Live, per release |
| **Windows / macOS** | download `.exe` / `.dmg` | `package.yml` | Live, per release |
| **Homebrew** (macOS) | `brew install --cask wleeaf/tap/projectum` | [`wleeaf/homebrew-tap`](https://github.com/wleeaf/homebrew-tap) → `Casks/projectum.rb` | Live |
| **Scoop** (Windows) | `scoop bucket add wleeaf …; scoop install projectum` | [`wleeaf/scoop-bucket`](https://github.com/wleeaf/scoop-bucket) → `bucket/projectum.json` | Live |
| **AUR** (Arch) | `yay -S projectum` | `packaging/aur/` + [aur.archlinux.org](https://aur.archlinux.org/packages/projectum) | **Live** |
| **Flathub** (Linux) | (not distributed) | `packaging/flatpak/` (reference only) | Declined — AI policy |

## Live channels

### Homebrew — `wleeaf/homebrew-tap`
```bash
brew install --cask wleeaf/tap/projectum
```
Cask pinned to the `.dmg` sha256, with a Gatekeeper caveat (unsigned) and a
`livecheck` block. Per release: bump `version` + `sha256` in `Casks/projectum.rb`, push.

### Scoop — `wleeaf/scoop-bucket`
```powershell
scoop bucket add wleeaf https://github.com/wleeaf/scoop-bucket
scoop install projectum
```
Manifest has `checkver` + `autoupdate` against GitHub releases, so it tracks new
versions automatically — **no manual bump needed**.

## Ready, needs one step

### AUR — live (maintainer `wleeaf`)
Published at [aur.archlinux.org/packages/projectum](https://aur.archlinux.org/packages/projectum).
`packaging/aur/` is the source of truth. Each new release, re-push from a clone of
the AUR repo (SSH key `~/.ssh/id_ed25519` is already registered):
```bash
git clone ssh://aur@aur.archlinux.org/projectum.git aur-projectum
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO aur-projectum/
cd aur-projectum && git add -A && git commit -m "projectum <ver>" && git push
```
Before pushing a new version, bump `pkgver` + the tarball `sha256` in
`packaging/aur/PKGBUILD` and regenerate `.SRCINFO` (`makepkg --printsrcinfo`).
The AUR default branch is `master`.

### Flathub — declined (AI policy)
**Not being distributed.** Flathub's requirements forbid AI-assisted apps and
agent-opened PRs, so we're not submitting. The `packaging/flatpak/` files are
kept only as a **working reference** — the manifest is build-verified (6.10,
builds + launches in-sandbox), lint-clean bar the intentional `--filesystem=home`,
and its metadata passes `appstreamcli validate` + `desktop-file-validate`. If
Flathub ever relaxes the policy, it's ready to submit; reproduce the build with:
```bash
flatpak install flathub org.kde.Sdk//6.10 org.kde.Platform//6.10 io.qt.PySide.BaseApp//6.10
flatpak-builder --user --install --force-clean build-dir \
  packaging/flatpak/io.github.wleeaf.Projectum.yml
flatpak run io.github.wleeaf.Projectum
# pre-PR lints (run builddir from a path the sandbox can see, e.g. under $HOME):
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest \
  packaging/flatpak/io.github.wleeaf.Projectum.yml
```

**The PR** (I can do the fork + PR via the `wleeaf` gh login when you're ready):
1. Fork [flathub/flathub](https://github.com/flathub/flathub), branch
   `io.github.wleeaf.Projectum`, add the manifest, PR against the **`new-pr`** branch.
2. In the PR body, justify `--filesystem=home`: Projectum opens arbitrary project
   folders, writes a `.projectum.json` inside each, and the calendar scans several
   tracked folders at once — the file-chooser portal can't do that persistently.
3. On merge, Flathub creates `flathub/io.github.wleeaf.Projectum`; future updates
   go there (bump the tarball URL + sha256, the yt-dlp wheel when it changes, and
   the metainfo `<releases>` list).

## Per-release maintenance

When cutting a new release, update each channel:

| Channel | Action |
|---|---|
| PyPI / AppImage / exe / dmg | Automatic via CI on the release. |
| **Scoop** | Automatic (`checkver`/`autoupdate`). |
| **Homebrew** | Bump `version` + `.dmg` `sha256` in the tap, push. |
| **AUR** | Bump `pkgver`, `updpkgsums`, `makepkg --printsrcinfo > .SRCINFO`, push. |
| **Flathub** | Bump the manifest's tarball URL + sha256 (and the yt-dlp wheel when it changes) + the metainfo `<releases>` list, PR. |

Manual ones are Homebrew, AUR, Flathub — candidates to automate from the release workflow.

## Signing

The Windows `.exe` and macOS `.dmg` are **unsigned**: first launch warns
(SmartScreen / Gatekeeper), and signing-required stores (Microsoft Store, Mac
App Store) and a clean winget listing are blocked. Unblock with an Apple
Developer membership ($99/yr) and a Windows code-signing cert (Azure Trusted
Signing is now nearly free for individuals; an OV cert is ~$200+/yr). PyPI, AUR,
Scoop, and Flathub need no OS code-signing; the Homebrew cask works unsigned but
shows the Gatekeeper prompt.
