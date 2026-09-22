"""Install release artifacts in a clean venv and boot outside the checkout.

Usage: python packaging/check_install.py dist
Also accepts a single wheel or sdist, e.g. to check a downloaded PyPI release.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


SMOKE = r'''
from importlib.metadata import distribution
from pathlib import Path
import sys
import runpy

import projectum
from projectum import app as appmod
from PySide6.QtCore import QTimer, QLibraryInfo, QPluginLoader
from PySide6.QtGui import QIcon
import yt_dlp

# An import from the checkout would hide missing modules/assets in the wheel.
assert Path(projectum.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
dist = distribution("projectum")
assert dist.version == projectum.__version__
entry = next(e for e in dist.entry_points if e.group == "console_scripts" and e.name == "projectum")
assert entry.load() is appmod.run
assert appmod.ICON_PATH.is_file(), "Missing packaged application icon"
if sys.platform == "win32":
    plugin_path = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms/qwindows.dll"
    plugin = QPluginLoader(str(plugin_path))
    assert plugin.load(), plugin.errorString()

# Exercise the installed entry point and its event loop, closing before the
# delayed network update check. Config and cwd are temporary and isolated.
class SmokeWindow(appmod.MainWindow):
    def __init__(self):
        super().__init__()
        assert not QIcon(str(appmod.ICON_PATH)).isNull()
        QTimer.singleShot(150, self.close)

appmod.MainWindow = SmokeWindow
if sys.argv[1:] == ["module"]:
    sys.argv.pop()
    try:
        runpy.run_module("projectum", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
else:
    assert entry.load()() == 0
print("Installed Projectum", dist.version, "booted successfully", flush=True)
'''


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python packaging/check_install.py DIST_DIR_OR_ARTIFACT")
    source = Path(sys.argv[1]).resolve()
    if source.is_dir():
        wheels = sorted(source.glob("projectum-*.whl"))
        sdists = sorted(source.glob("projectum-*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise SystemExit("Expected exactly one Projectum wheel and one sdist")
        artifacts = wheels + sdists
    elif source.is_file():
        artifacts = [source]
    else:
        raise SystemExit(f"No artifact found at {source}")

    with tempfile.TemporaryDirectory(prefix="projectum-install-") as directory:
        root = Path(directory)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.update(QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(root / "config"))

        def run(*args: str) -> None:
            subprocess.run([str(python), *args], cwd=root, env=env, check=True, timeout=600)

        run("-m", "pip", "install", "--upgrade", "pip")
        for artifact in artifacts:
            print(f"Checking installation of {artifact.name}", flush=True)
            run("-m", "pip", "install", "--force-reinstall", str(artifact))
            run("-m", "pip", "check")
            run("-I", "-c", SMOKE)
            run("-I", "-c", SMOKE, "module")


if __name__ == "__main__":
    main()
