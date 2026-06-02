"""Shared pytest fixtures.

The Qt tests run on the ``offscreen`` platform so they need no display. We set
it before any PySide6 import so the tests are headless by default, locally and
in CI.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication for the whole test session."""
    from PySide6.QtWidgets import QApplication
    from projectum import theme

    app = QApplication.instance() or QApplication([])
    theme.apply_theme("dark")
    app.setStyleSheet(theme.build_stylesheet())
    return app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A MainWindow with dialogs stubbed and isolated config, pointed at a
    fresh temp folder containing the given subfolders via ``window.open(...)``.
    """
    # Isolate persisted state (geometry/settings/recent folders).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    import projectum.app as appmod

    monkeypatch.setattr(appmod.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(appmod.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        appmod.QMessageBox, "question",
        staticmethod(lambda *a, **k: appmod.QMessageBox.StandardButton.Yes),
    )
    win = appmod.MainWindow()
    win.resize(1180, 760)
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    qapp.processEvents()
