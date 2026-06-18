"""Background check for a newer GitHub release.

A single read-only GET to the public GitHub Releases API — no auth, no
telemetry, no identifying data. Runs on the thread pool and fails silently
(offline, rate-limited, parse error → no banner). The version comparison is a
pure function so it's easy to test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

GITHUB_REPO = "wleeaf/projectum"
_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
_APPIMAGE_ASSET = "Projectum-x86_64.AppImage"


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.7.0"`` → ``(1, 7, 0)``. Stops at the first non-numeric segment
    (so pre-release suffixes are ignored), returns ``()`` if unparseable."""
    parts: list[int] = []
    for seg in (text or "").strip().lstrip("vV").split("."):
        m = re.match(r"\d+", seg)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer version than ``current``."""
    lv = parse_version(latest)
    return bool(lv) and lv > parse_version(current)


class UpdateCheckSignals(QObject):
    # Emitted only when a newer release exists: (tag, release page URL).
    update_available = Signal(str, str)


class UpdateCheckRunnable(QRunnable):
    """Fetches the latest release tag off the UI thread."""

    def __init__(self, current_version: str):
        super().__init__()
        self.current_version = current_version
        self.signals = UpdateCheckSignals()

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                _LATEST_URL,
                headers={
                    "User-Agent": "Projectum-update-check",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return  # fail-silent: offline / rate-limited / bad response
        if not isinstance(data, dict):
            return
        tag = str(data.get("tag_name") or "")
        url = str(data.get("html_url") or _RELEASES_PAGE)
        if tag and is_newer(tag, self.current_version):
            self.signals.update_available.emit(tag, url)


# ──────────────────────── self-update ────────────────────────


def _is_conda_package() -> bool:
    """True if Projectum was installed by conda — i.e. a conda-meta record for
    it exists in this prefix. (A plain pip-into-a-conda-env install has no such
    record, so this stays specific to genuinely conda-managed installs.)"""
    try:
        meta = Path(sys.prefix) / "conda-meta"
        return meta.is_dir() and any(meta.glob("projectum-*.json"))
    except OSError:
        return False


def _is_git_checkout() -> bool:
    """True if Projectum is running from a source checkout (a ``.git`` sits
    beside the package), where ``git pull`` is the right update path."""
    return (Path(__file__).resolve().parent.parent / ".git").exists()


def _is_externally_managed() -> bool:
    """True if this interpreter is marked externally-managed (PEP 668) — a
    distro- or Homebrew-packaged Python where ``pip install --upgrade`` is
    refused. Mirrors pip's own check, so it predicts whether a pip self-update
    would actually succeed. A virtualenv is never marked, so source/pip installs
    in a venv stay upgradable."""
    for key in ("stdlib", "platstdlib"):
        try:
            path = sysconfig.get_path(key)
        except (KeyError, OSError):
            continue
        if path and (Path(path) / "EXTERNALLY-MANAGED").exists():
            return True
    return False


def install_channel() -> str:
    """How this Projectum is installed, which decides the update strategy:

      'appimage'  the AppImage           — replace the file in place
      'frozen'    PyInstaller .exe/.app   — manual download (can't self-replace)
      'git'       a source checkout       — git pull --ff-only
      'pip'       a writable pip env      — pip install --upgrade
      'managed'   Flatpak/Snap/conda/distro package — the package manager owns
                  updates; we must never touch the install

    The Flatpak/Snap/conda signals are checked before 'git' because they're
    specific enough never to fire on a dev checkout; the generic PEP 668 marker
    is checked after 'git' so a checkout run from a system Python still updates
    via git rather than being mistaken for a distro package.
    """
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if getattr(sys, "frozen", False):
        return "frozen"
    if (os.environ.get("FLATPAK_ID") or os.environ.get("SNAP")
            or Path("/.flatpak-info").exists() or _is_conda_package()):
        return "managed"
    if _is_git_checkout():
        return "git"
    if _is_externally_managed():
        return "managed"
    return "pip"


def can_auto_update() -> bool:
    """In-place self-update is only safe where we own the install. Frozen
    bundles can't replace their running binary; 'managed' installs belong to a
    package manager (Flatpak, Snap, conda, distro) that owns updates — both
    keep their hands off and let the user act instead."""
    return install_channel() in ("appimage", "git", "pip")


def _apply_appimage(tag: str) -> tuple[bool, str]:
    target = Path(os.environ["APPIMAGE"])
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{_APPIMAGE_ASSET}"
    req = urllib.request.Request(url, headers={"User-Agent": "Projectum-update"})
    # Download beside the target so os.replace stays atomic (same filesystem).
    fd, tmp = tempfile.mkstemp(prefix=".projectum-update-", dir=target.parent)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(resp, out)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True, str(target)


def _apply_git() -> tuple[bool, str]:
    root = Path(__file__).resolve().parent.parent
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=120,
        )
    if git("status", "--porcelain").stdout.strip():
        return False, "working tree has local changes"
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        return False, f"not on main (on {branch})"
    pull = git("pull", "--ff-only", "origin", "main")
    if pull.returncode != 0:
        return False, pull.stderr.strip() or "git pull failed"
    return True, str(root)


def _apply_pip() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "projectum"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        return False, proc.stderr.strip()[-200:] or "pip install failed"
    return True, "pip"


class UpdateApplySignals(QObject):
    finished = Signal(bool, str)  # (ok, detail — install path or error)


class UpdateApplyRunnable(QRunnable):
    """Downloads/installs the new version in place, off the UI thread.

    The running process keeps executing the old code; the update takes
    effect on the next launch (the app offers a Restart button).
    """

    def __init__(self, tag: str):
        super().__init__()
        self.tag = tag
        self.signals = UpdateApplySignals()

    def run(self) -> None:
        try:
            apply_fn = {
                "appimage": lambda: _apply_appimage(self.tag),
                "git": _apply_git,
                "pip": _apply_pip,
            }.get(install_channel())
            ok, detail = apply_fn() if apply_fn else (False, "unsupported install")
        except Exception as e:  # network error, perms, …
            ok, detail = False, str(e)
        self.signals.finished.emit(ok, detail)
