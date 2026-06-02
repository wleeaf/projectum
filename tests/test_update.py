"""Update-check: version compare (pure) + banner / opt-out behavior (offscreen)."""

from projectum.app import load_state, save_state
from projectum.update import is_newer, parse_version
from projectum.widgets import SettingsDialog, UpdateBanner


# ── pure version logic ──

def test_parse_version():
    assert parse_version("v1.7.0") == (1, 7, 0)
    assert parse_version("1.6.0") == (1, 6, 0)
    assert parse_version("v2.0") == (2, 0)
    assert parse_version("1.7.0-rc1") == (1, 7, 0)  # pre-release suffix ignored
    assert parse_version("garbage") == ()


def test_is_newer():
    assert is_newer("v1.7.0", "1.6.0")
    assert is_newer("1.6.1", "1.6.0")
    assert not is_newer("1.6.0", "1.6.0")
    assert not is_newer("1.5.0", "1.6.0")
    assert not is_newer("garbage", "1.6.0")  # unparseable -> never "newer"


# ── banner widget ──

def test_banner_show(qapp):
    b = UpdateBanner()
    assert not b.isVisible()
    b.show_update("v9.9.9")
    assert "9.9.9" in b.label.text() and b.isVisible()
    b.deleteLater()


# ── MainWindow flow ──

def test_update_banner_flow_and_dismissal(window, qapp):
    window._on_update_available("v9.9.9", "https://example.test/r")
    assert window._update_banner.isVisible()
    assert window._update_version == "v9.9.9"
    assert window._update_url == "https://example.test/r"

    # Dismiss hides it and remembers the version.
    window._dismiss_update()
    assert not window._update_banner.isVisible()
    assert load_state().get("update_dismissed") == "v9.9.9"

    # The same version no longer re-notifies...
    window._on_update_available("v9.9.9", "https://example.test/r")
    assert not window._update_banner.isVisible()
    # ...but a newer one does.
    window._on_update_available("v9.9.10", "https://example.test/r2")
    assert window._update_banner.isVisible()


def test_update_check_respects_opt_out(window, qapp, monkeypatch):
    class FakePool:
        def __init__(self):
            self.started = []

        def start(self, runnable):
            self.started.append(runnable)

    fake = FakePool()
    monkeypatch.setattr(window, "_size_pool", fake)

    save_state({"settings": {"check_updates": False}})
    window._maybe_check_updates()
    assert fake.started == []  # opted out -> no network runnable

    save_state({"settings": {"check_updates": True}})
    window._maybe_check_updates()
    assert len(fake.started) == 1  # opted in -> check kicked off


# ── settings toggle ──

def test_settings_has_update_toggle(qapp):
    d = SettingsDialog("dark", "Inter", 13, current_check_updates=True)
    assert d.update_check.isChecked()
    assert d._current_selection()["check_updates"] is True
    # toggling it marks the dialog dirty + flows into the staged selection
    d.update_check.setChecked(False)
    assert d.apply_btn.isEnabled()
    assert d._current_selection()["check_updates"] is False
    d.deleteLater()
