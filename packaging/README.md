# Packaging & distribution

Scaffolding for shipping Projectum on multiple platforms. Each channel is a
**draft you activate** when the corresponding account/setup is ready.

| Channel | Files | Effort | Status |
|---|---|---|---|
| **AppImage** (Linux) | `appimage/` + `.github/workflows/release.yml` | — | **Live** — built & attached to every release |
| **Windows / macOS** | `.github/workflows/package.yml` | — | **Live** — built & attached to every release |
| **PyPI** | `.github/workflows/publish-pypi.yml` | — | **Live** — `pip install projectum` |
| **Homebrew** (macOS) | [`wleeaf/homebrew-tap`](https://github.com/wleeaf/homebrew-tap) | — | **Live** — `brew install --cask wleeaf/tap/projectum` |
| **Scoop** (Windows) | [`wleeaf/scoop-bucket`](https://github.com/wleeaf/scoop-bucket) | — | **Live** — `scoop bucket add wleeaf …; scoop install projectum` |
| **AUR** | [aur.archlinux.org/packages/projectum](https://aur.archlinux.org/packages/projectum) | — | **Live** — `yay -S projectum` |
| **Flathub** | `flatpak/` | Medium | **Ready** — build-verified (flatpak-builder + launch); needs a review PR |

Verified locally: `projectum` is free on PyPI, and `python -m build` + `twine check` pass.

## PyPI — `pip install projectum`

1. Create the project's **Trusted Publisher** on PyPI (no API token needed):
   PyPI → your account → *Publishing* → add a pending publisher:
   - PyPI Project Name: `projectum`
   - Owner: `wleeaf` · Repository: `projectum`
   - Workflow filename: `publish-pypi.yml` · Environment: *(leave blank)*
2. Publish a GitHub release (or run the workflow manually). `publish-pypi.yml`
   builds the sdist + wheel and uploads via OIDC.

Manual one-off (needs a token): `python -m build && twine upload dist/*`.

## Homebrew & Scoop — live

Both are personal taps, already published and pinned to the current release:

- **Homebrew** ([`wleeaf/homebrew-tap`](https://github.com/wleeaf/homebrew-tap)) —
  `Casks/projectum.rb`. Each release, bump `version` + `sha256` (the `.dmg`'s) and push.
- **Scoop** ([`wleeaf/scoop-bucket`](https://github.com/wleeaf/scoop-bucket)) —
  `bucket/projectum.json`. `checkver`/`autoupdate` track GitHub releases, so
  `scoop update` picks up new versions without a manual bump.

`aur/` and `flatpak/` here are the in-repo source of truth; the live taps are the
copies above.

## AUR — `aur/PKGBUILD` (+ `.SRCINFO`)

Both files are **already pinned** to the current release (real tarball sha256).
To publish — one-time, needs an [AUR account](https://aur.archlinux.org) with your
SSH key registered:

```bash
git clone ssh://aur@aur.archlinux.org/projectum.git aur-projectum
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO aur-projectum/
cd aur-projectum && git add -A && git commit -m "projectum 2.3.0" && git push
```

Test first on Arch: `makepkg -si` in a clean checkout. Each release: bump
`pkgver`, `updpkgsums`, `makepkg --printsrcinfo > .SRCINFO`.

## Flathub — `flatpak/`

App ID `io.github.wleeaf.Projectum`. The manifest is **fully pinned** (yt-dlp
wheel + the v2.3.0 source tarball, both with sha256) and the metadata passes
`appstreamcli validate` and `desktop-file-validate`. Remaining (needs the Flatpak
toolchain, which CI/this repo doesn't have):

1. Build/test on a machine with `flatpak-builder`:
   ```bash
   flatpak install flathub org.kde.Sdk//6.8 org.kde.Platform//6.8 io.qt.PySide.BaseApp//6.8
   flatpak-builder --user --install --force-clean build-dir flatpak/io.github.wleeaf.Projectum.yml
   flatpak run io.github.wleeaf.Projectum
   ```
2. Submit a PR to [flathub/flathub](https://github.com/flathub/flathub) (a
   `new-pr` branch with the manifest) and iterate with reviewers.

> **Signing note:** the Windows `.exe` and macOS `.dmg` are **unsigned**, so the
> OS-native stores (winget, Microsoft Store, Mac App Store) would warn users or
> reject them without paid signing certificates. The Homebrew cask carries a
> Gatekeeper caveat; PyPI, AUR, Scoop and Flathub don't require OS code-signing.
