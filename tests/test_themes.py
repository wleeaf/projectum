"""Readability gate: every theme must clear minimum WCAG contrast for each
text role.

Floors are decided per *role*, anchored to WCAG, and applied globally — no
per-theme exceptions (those rot). A new theme that can't meet a floor without
losing its identity is a signal to revisit the floor, not to special-case the
theme here.
"""

import pytest

from projectum import theme as T

BODY = 4.5    # primary text (TEXT on a background)
DEEMPH = 3.0  # de-emphasized / large-bold UI text

ALL_THEMES = list(T.THEMES.keys())


@pytest.mark.parametrize("key", ALL_THEMES)
def test_body_text_readable(key):
    t = T.THEMES[key]
    for bg in ("BG", "SURFACE", "SURFACE_2"):
        c = T.contrast_ratio(t["TEXT"], t[bg])
        assert c >= BODY, f"{key}: TEXT on {bg} = {c:.2f} (< {BODY})"


@pytest.mark.parametrize("key", ALL_THEMES)
def test_deemphasized_and_semantic_text_readable(key):
    # Bold semantic text (status / git / error / tested) sits on SURFACE.
    t = T.THEMES[key]
    for role in ("SUCCESS", "WARNING", "DANGER", "INFO"):
        c = T.contrast_ratio(t[role], t["SURFACE"])
        assert c >= DEEMPH, f"{key}: {role} on SURFACE = {c:.2f} (< {DEEMPH})"


@pytest.mark.parametrize("key", ALL_THEMES)
def test_muted_text_readable_and_ordered(key):
    # Muted/dim text is normal-size body copy (hints, metadata, subtitles),
    # so it gets the full BODY floor, on both backgrounds it appears over.
    t = T.THEMES[key]
    for role in ("TEXT_DIM", "TEXT_MUTED"):
        for bg in ("BG", "SURFACE"):
            c = T.contrast_ratio(t[role], t[bg])
            assert c >= BODY, f"{key}: {role} on {bg} = {c:.2f} (< {BODY})"
    # The de-emphasis hierarchy must hold: DIM reads stronger than MUTED.
    dim = min(T.contrast_ratio(t["TEXT_DIM"], t[bg]) for bg in ("BG", "SURFACE"))
    muted = min(T.contrast_ratio(t["TEXT_MUTED"], t[bg]) for bg in ("BG", "SURFACE"))
    assert dim > muted, f"{key}: TEXT_DIM ({dim:.2f}) not above TEXT_MUTED ({muted:.2f})"


@pytest.mark.parametrize("key", ALL_THEMES)
def test_accent_pairs_readable(key):
    t = T.THEMES[key]
    # Primary buttons / selected rows / checked chips: BG-colored text on ACCENT.
    assert T.contrast_ratio(t["BG"], t["ACCENT"]) >= DEEMPH, f"{key}: BG on ACCENT"
    # Accent-colored text / tab underline on the window background.
    assert T.contrast_ratio(t["ACCENT"], t["BG"]) >= DEEMPH, f"{key}: ACCENT on BG"


def test_every_theme_has_the_full_key_set():
    required = set(T.DARK_THEME.keys())
    for key, t in T.THEMES.items():
        assert set(t.keys()) == required, f"{key} key mismatch: {set(t) ^ required}"


def test_labels_and_themes_in_sync():
    assert {k for k, _ in T.THEME_LABELS} == set(T.THEMES)


def test_legible_ink_reaches_target_and_is_idempotent():
    bg = "#eeeeee"
    assert T.contrast_ratio(T.legible_ink("#f5b8e2", bg, 3.5), bg) >= 3.5
    # An already-contrasting color comes back unchanged.
    assert T.legible_ink("#000000", "#ffffff", 3.5) == "#000000"
