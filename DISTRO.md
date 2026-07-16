# Distribution status

State of every channel Projectum ships through, and what's left to do. Last
updated **2026-06-18**, against release **v2.4.0**.

> The in-repo `packaging/` files are the source of truth. The Homebrew/Scoop
> tap repos are live deployment copies. See also `packaging/README.md`.

## Pick up here next visit

- [ ] **AUR** — **build-verified** (makepkg + namcap clean in an Arch container; fixed the `pyside6` dep name and added `hicolor-icon-theme`). Just needs the push once you register (needs your AUR account; steps below).
- [ ] **Flathub** — **build-verified** (`flatpak-builder` builds it and the app launches in-sandbox). Just needs the PR to `flathub/flathub` (steps below).
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
| **AUR** (Arch) | `yay -S projectum` | `packaging/aur/` (PKGBUILD + .SRCINFO) | Build-verified — needs push |
| **Flathub** (Linux) | `flatpak install …Projectum` | `packaging/flatpak/` | Build-verified — needs PR |

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

### AUR — needs your account
`packaging/aur/PKGBUILD` + `.SRCINFO` are pinned to 2.4.0 with the real tarball
sha256. One-time, with an [AUR account](https://aur.archlinux.org) + SSH key:
```bash
git clone ssh://aur@aur.archlinux.org/projectum.git aur-projectum
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO aur-projectum/
cd aur-projectum && git add -A && git commit -m "projectum 2.4.0" && git push
```
Test on Arch first: `makepkg -si` in a clean checkout.

### Flathub — needs the PR
App ID `io.github.wleeaf.Projectum`. Manifest is fully pinned (yt-dlp wheel +
v2.4.0 source tarball, both sha256) and **build-verified**: `flatpak-builder`
builds it clean and the app launches in-sandbox; metadata passes `appstreamcli
validate` and `desktop-file-validate`. To reproduce the build locally (the
`org.kde.*//6.8` + `io.qt.PySide.BaseApp//6.8` runtimes are already installed
on this machine):
```bash
flatpak install flathub org.kde.Sdk//6.8 org.kde.Platform//6.8 io.qt.PySide.BaseApp//6.8
flatpak-builder --user --install --force-clean build-dir \
  packaging/flatpak/io.github.wleeaf.Projectum.yml
flatpak run io.github.wleeaf.Projectum
```
Then submit a PR to [flathub/flathub](https://github.com/flathub/flathub) (a
`new-pr` branch with the manifest) and iterate with reviewers.

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
