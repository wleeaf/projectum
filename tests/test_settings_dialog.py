"""SettingsDialog: select-only dropdowns + staged Apply (no live apply)."""

from PySide6.QtWidgets import QComboBox, QFontComboBox

from projectum.widgets import SettingsDialog


def _dialog(qapp):
    return SettingsDialog("dark", "Inter", 13)


def test_controls_are_select_only_dropdowns(qapp):
    d = _dialog(qapp)
    assert isinstance(d.font_combo, QFontComboBox) and not d.font_combo.isEditable()
    assert isinstance(d.size_combo, QComboBox) and not d.size_combo.isEditable()
    # size dropdown holds integer px values
    assert all(isinstance(d.size_combo.itemData(i), int)
               for i in range(d.size_combo.count()))
    assert d.size_combo.currentData() == 13
    d.deleteLater()


def test_no_live_apply_only_on_button(qapp):
    d = _dialog(qapp)
    emitted = []
    d.settings_changed.connect(emitted.append)
    # Apply starts disabled (nothing changed yet).
    assert not d.apply_btn.isEnabled()
    # Changing a control must NOT emit, but must enable Apply.
    other = next(i for i in range(d.theme_combo.count())
                 if d._theme_keys[i] != "dark")
    d.theme_combo.setCurrentIndex(other)
    assert emitted == []
    assert d.apply_btn.isEnabled()
    # Apply emits exactly once, with the staged selection, then disables.
    d._on_apply()
    assert len(emitted) == 1
    assert emitted[0]["theme"] == d._theme_keys[other]
    assert emitted[0]["font_size"] == 13
    assert not d.apply_btn.isEnabled()
    d.deleteLater()


def test_reverting_selection_disables_apply(qapp):
    d = _dialog(qapp)
    start = d.size_combo.currentIndex()
    d.size_combo.setCurrentIndex((start + 1) % d.size_combo.count())
    assert d.apply_btn.isEnabled()
    d.size_combo.setCurrentIndex(start)  # back to the applied value
    assert not d.apply_btn.isEnabled()
    d.deleteLater()


def test_theme_items_have_swatch_icons(qapp):
    d = _dialog(qapp)
    assert not d.theme_combo.itemIcon(0).isNull()  # color-swatch preview
    d.deleteLater()
