# Packaging & distribution

Scaffolding for shipping Projectum on multiple platforms. Each channel is a
**draft you activate** when the corresponding account/setup is ready.

| Channel | Files | Effort | Status |
|---|---|---|---|
| **AppImage** (Linux) | `appimage/` + `.github/workflows/release.yml` | — | **Live** — built & attached to every release |
| **Windows / macOS** | `.github/workflows/package.yml` | — | **Live** — manual `gh workflow run package.yml` |
| **PyPI** | `.github/workflows/publish-pypi.yml` | Low | Ready — needs Trusted Publishing configured |
| **AUR** | `aur/PKGBUILD` | Low | Draft — needs an AUR account + `.SRCINFO` |
| **Flathub** | `flatpak/` | Medium‑high | Draft — needs deps vendored + review |

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

## AUR — `aur/PKGBUILD`

1. Each release: bump `pkgver`, run `updpkgsums` (real sha256), then
   `makepkg --printsrcinfo > .SRCINFO`.
2. `git push` the `PKGBUILD` + `.SRCINFO` to `ssh://aur@aur.archlinux.org/projectum.git`
   (requires an AUR account with your SSH key).
3. Test first: `makepkg -si` in a clean checkout.

## Flathub — `flatpak/`

App ID: `io.github.wleeaf.Projectum`. The manifest is a **starting point** (see
its header). Remaining work:

1. Vendor `yt-dlp` + deps for an offline build:
   `python flatpak-pip-generator --runtime org.kde.Sdk//6.8 yt-dlp`, then swap
   in the generated module. (PySide6 comes from `io.qt.PySide.BaseApp`.)
2. Validate: `appstreamcli validate flatpak/*.metainfo.xml` and
   `desktop-file-validate flatpak/*.desktop`.
3. Build/test: `flatpak-builder --user --install --force-clean build-dir
   flatpak/io.github.wleeaf.Projectum.yml`, then run it.
4. Submit a PR to [flathub/flathub](https://github.com/flathub/flathub) and
   iterate with reviewers.

> **Signing note:** the Windows `.exe` and macOS `.dmg` are **unsigned**, so the
> OS-native stores (winget, Homebrew cask, Mac App Store) would warn users or
> reject them without paid signing certificates. PyPI, AUR and Flathub don't
> require OS code-signing.
