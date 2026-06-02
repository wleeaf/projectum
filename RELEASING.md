# Releasing Projectum

Cutting a release is **one command**. Everything else — building all three
platform binaries, attaching them, and publishing to PyPI — happens in CI.

## 1. Prepare the version

Bump the version in **both** places (they must match):

- `pyproject.toml` → `version = "X.Y.Z"`
- `projectum/__init__.py` → `__version__ = "X.Y.Z"`

Add a `## [X.Y.Z]` section to `CHANGELOG.md` (with the `[X.Y.Z]: …/releases/tag/vX.Y.Z`
link reference at the bottom of its block), then commit and push to `main`:

```bash
git add -A && git commit -m "Release vX.Y.Z: <one-line summary>"
git push origin main
```

Wait for **CI** (ci.yml) to go green on that commit before tagging.

## 2. Cut the release — one command

```bash
gh release create vX.Y.Z --target main \
  --title "Projectum vX.Y.Z" \
  --notes-file <notes.md>
```

This is the **load-bearing** step. In order, it:

1. Creates and pushes the `vX.Y.Z` tag at `main`.
2. Creates a **published** GitHub Release carrying your notes.

Those two events fan out to the four workflows automatically:

| Trigger | Workflow | Produces |
|---|---|---|
| tag push (`v*`) | `release.yml` | `Projectum-x86_64.AppImage` → attached |
| tag push (`v*`) | `package.yml` | `Projectum-windows-x64.exe`, `Projectum-macos.dmg` → attached |
| release published | `publish-pypi.yml` | PyPI publish (Trusted Publishing, no token) |

No need to pass any files to `gh release create` or to run `package.yml`
manually — the builders attach their own assets to the release you just made.

## 3. Verify

```bash
gh run watch --exit-status              # watch the triggered runs
gh release view vX.Y.Z --json assets --jq '.assets[].name'
# expect: Projectum-x86_64.AppImage, Projectum-windows-x64.exe, Projectum-macos.dmg
pip index versions projectum            # or check https://pypi.org/project/projectum/
```

## ⚠️ The one trap: never bare-push a `v*` tag

```bash
git tag vX.Y.Z && git push origin vX.Y.Z   # ❌ DON'T
```

A bare tag push fires the **builders** (they'd create a note-less release via
`softprops/action-gh-release`) but **not** `publish-pypi.yml` — that's gated on
`release: published`, which only `gh release create` (or the web UI) emits. The
result is a release with no notes and no PyPI publish. Always use
`gh release create`.

## Notes

- **Asset names are a hard contract.** The README download commands and the
  `…/releases/latest/download/<name>` URLs depend on the exact filenames in the
  table above. If you rename an asset, update `README.md` in the same change.
- **Windows/macOS binaries are unsigned.** They're built and smoke-tested
  (offscreen launch) in CI, but not notarized — first launch warns on the OS.
- Manual platform rebuilds (without a release) still work:
  `gh workflow run package.yml -f platform=windows` — artifacts only, nothing
  attached.
