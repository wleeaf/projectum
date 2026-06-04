"""Calendar: pure layout/scan/write-back logic + widget and integration."""

import json
from datetime import date

from projectum import calendar as cal
from projectum.links import date_ref, make_ref
from projectum.store import ProjectStore


def _folder(tmp_path, name, subdirs=()):
    f = tmp_path / name
    f.mkdir()
    for s in subdirs:
        (f / s).mkdir()
    return f


def _si(start, end="", kind="todo", home="/h", key="k", title="t", done=False):
    return cal.ScheduledItem(home, kind, key, title, start, end, done)


# ── pure date helpers ──

def test_parse_date():
    assert cal.parse_date("2026-06-04") == date(2026, 6, 4)
    assert cal.parse_date("") is None
    assert cal.parse_date("nonsense") is None
    assert cal.parse_date(None) is None


def test_month_grid_start():
    # June 2026 starts on a Monday -> grid starts on the 1st.
    assert cal.month_grid_start(2026, 6) == date(2026, 6, 1)
    # July 2026 starts on a Wednesday -> grid backs up to Mon Jun 29.
    assert cal.month_grid_start(2026, 7) == date(2026, 6, 29)


# ── layout_month edge cases ──

def test_layout_single_day():
    gs = cal.month_grid_start(2026, 6)
    bars = cal.layout_month([_si("2026-06-03")], gs)
    assert len(bars) == 1
    b = bars[0]
    assert (b.week, b.col_start, b.col_end) == (0, 2, 2)
    assert not b.continues_left and not b.continues_right


def test_layout_week_boundary():
    gs = cal.month_grid_start(2026, 6)
    bars = sorted(cal.layout_month([_si("2026-06-06", "2026-06-09")], gs),
                  key=lambda b: b.week)
    assert len(bars) == 2
    assert (bars[0].week, bars[0].col_start, bars[0].col_end) == (0, 5, 6)
    assert bars[0].continues_right and not bars[0].continues_left
    assert (bars[1].week, bars[1].col_start, bars[1].col_end) == (1, 0, 1)
    assert bars[1].continues_left and not bars[1].continues_right


def test_layout_clips_before_and_after_window():
    gs = cal.month_grid_start(2026, 6)  # 2026-06-01 .. 2026-07-12
    before = cal.layout_month([_si("2026-05-28", "2026-06-02")], gs)[0]
    assert before.week == 0 and before.col_start == 0 and before.continues_left
    after = cal.layout_month([_si("2026-07-10", "2026-07-20")], gs)[0]
    assert after.week == 5 and after.col_end == 6 and after.continues_right


def test_layout_three_overlapping_lanes():
    gs = cal.month_grid_start(2026, 6)
    bars = cal.layout_month([
        _si("2026-06-02", "2026-06-04"),
        _si("2026-06-03", "2026-06-05"),
        _si("2026-06-01", "2026-06-03"),
    ], gs)
    assert sorted(b.lane for b in bars) == [0, 1, 2]


def test_layout_spanning_bar_gets_low_lane():
    # A multi-day bar sharing a busy start day must stay visible across its
    # whole span (low lane), not be buried by the single-day items.
    gs = cal.month_grid_start(2026, 6)
    items = [_si("2026-06-15", "2026-06-19"),  # spanning
             _si("2026-06-15"), _si("2026-06-15"), _si("2026-06-15")]
    bars = cal.layout_month(items, gs)
    spanning = [b for b in bars if b.item_index == 0]
    assert spanning and all(b.lane == 0 for b in spanning)


def test_layout_ignores_unscheduled():
    gs = cal.month_grid_start(2026, 6)
    assert cal.layout_month([_si(""), _si("2025-01-01", "2025-01-02")], gs) == []


def test_items_on_day_inclusive():
    items = [_si("2026-06-10", "2026-06-12")]
    assert len(cal.items_on_day(items, date(2026, 6, 10))) == 1
    assert len(cal.items_on_day(items, date(2026, 6, 12))) == 1
    assert cal.items_on_day(items, date(2026, 6, 13)) == []


def test_unscheduled_filter():
    items = [_si("2026-06-01"), _si(""), _si("")]
    assert len(cal.unscheduled(items)) == 2


# ── scan ──

def test_scan_disk_crossfolder_and_exclude(tmp_path):
    fa = _folder(tmp_path, "A", ["src"])
    fb = _folder(tmp_path, "B", ["src"])
    sa = ProjectStore(fa); sa.projects["src"].start = "2026-06-02"; sa.save()
    sb = ProjectStore(fb); sb.projects["src"].start = "2026-06-09"; sb.save()
    # exclude A -> only B scanned; collision-safe (two distinct "src").
    items, skipped = cal.scan_disk([str(fa) + "/", str(fb)],
                                   cal.resolved_path(str(fa)))
    assert skipped == []
    srcs = [i for i in items if i.key == "src"]
    assert len(srcs) == 1 and cal.resolved_path(srcs[0].home) == cal.resolved_path(str(fb))


def test_scan_disk_dedups_by_resolved_path(tmp_path):
    fa = _folder(tmp_path, "A", ["src"])
    sa = ProjectStore(fa); sa.projects["src"].start = "2026-06-02"; sa.save()
    items, _ = cal.scan_disk([str(fa), str(fa) + "/", str(fa)])
    assert len([i for i in items if i.key == "src"]) == 1


def test_scan_disk_failsoft(tmp_path):
    good = _folder(tmp_path, "good", ["p"])
    ProjectStore(good).save()
    bad = tmp_path / "bad"; bad.mkdir()
    (bad / ".projectum.json").write_text("{ not valid json ")
    # A corrupt folder must not break the scan for the rest.
    items, skipped = cal.scan_disk([str(good), str(bad)])
    assert any(cal.resolved_path(i.home) == cal.resolved_path(str(good)) for i in items)


# ── apply_dates routing ──

def test_apply_dates_live_store(tmp_path):
    fa = _folder(tmp_path, "A", ["alpha"])
    live = ProjectStore(fa)
    item = cal.ScheduledItem(str(fa) + "/", "project", "alpha", "alpha")  # mismatched form
    assert cal.apply_dates(live, item, "2026-06-20", "2026-06-25")
    assert live.projects["alpha"].start == "2026-06-20"
    # a later live save must not clobber it
    live.add_todo("x"); live.save()
    assert ProjectStore(fa).projects["alpha"].start == "2026-06-20"


def test_apply_dates_on_demand_folder(tmp_path):
    fa = _folder(tmp_path, "A", ["alpha"])      # live
    fb = _folder(tmp_path, "B", ["beta"])        # on-demand
    live = ProjectStore(fa)
    item = cal.ScheduledItem(str(fb), "project", "beta", "beta")
    assert cal.apply_dates(live, item, "2026-07-01", "")  # single day -> end := start
    reloaded = ProjectStore(fb)
    assert reloaded.projects["beta"].start == "2026-07-01" == reloaded.projects["beta"].end
    assert "alpha" not in [n for n, p in ProjectStore(fa).projects.items() if p.start]


def test_apply_dates_orphan(tmp_path):
    fb = _folder(tmp_path, "B", ["beta"])
    sb = ProjectStore(fb); sb.projects["beta"].start = "2026-06-01"; sb.save()
    (fb / "beta").rmdir()  # beta now orphaned
    item = next(i for i in cal.scan_disk([str(fb)])[0] if i.key == "beta")
    assert item.orphan
    assert cal.apply_dates(None, item, "2026-08-01", "2026-08-03")
    raw = json.loads((fb / ".projectum.json").read_text())
    assert raw["_orphans"]["beta"]["start"] == "2026-08-01"


def test_apply_dates_normalizes_and_unschedules(tmp_path):
    fa = _folder(tmp_path, "A", ["alpha"])
    live = ProjectStore(fa)
    item = cal.ScheduledItem(str(fa), "project", "alpha", "alpha")
    # reversed range gets swapped
    cal.apply_dates(live, item, "2026-06-10", "2026-06-05")
    assert live.projects["alpha"].start == "2026-06-05"
    assert live.projects["alpha"].end == "2026-06-10"
    # empty start clears both
    cal.apply_dates(live, item, "", "2026-06-10")
    assert live.projects["alpha"].start == "" and live.projects["alpha"].end == ""


def test_collect_items_combines_live_and_disk(tmp_path):
    fa = _folder(tmp_path, "A", ["alpha"])
    fb = _folder(tmp_path, "B", ["beta"])
    sa = ProjectStore(fa); sa.projects["alpha"].start = "2026-06-02"; sa.save()
    sb = ProjectStore(fb); sb.projects["beta"].start = "2026-06-05"; sb.save()
    live = ProjectStore(fa)
    items, _ = cal.collect_items(live, [str(fa), str(fb)])
    keys = {(cal.resolved_path(i.home), i.key) for i in items if i.kind == "project"}
    assert (cal.resolved_path(str(fa)), "alpha") in keys
    assert (cal.resolved_path(str(fb)), "beta") in keys


# ── widgets ──

def test_calendarview_renders_dated_items(qapp):
    from projectum.widgets import CalendarView
    v = CalendarView()
    v.set_items([_si("2026-06-02", kind="project"), _si("2026-06-05", kind="todo")])
    assert len(v.grid._items) == 2     # dated items go on the grid (no tray anymore)
    v.deleteLater()


def test_schedule_dialog_emits_iso(qapp):
    from projectum.widgets import ScheduleDialog
    got = []
    d = ScheduleDialog("X", "2026-06-04", "2026-06-10")
    d.scheduled.connect(lambda s, e: got.append((s, e)))
    d._apply()
    assert got == [("2026-06-04", "2026-06-10")]
    d2 = ScheduleDialog("X", "2026-06-04", "2026-06-10")
    d2.scheduled.connect(lambda s, e: got.append((s, e)))
    d2._unschedule()
    assert got[-1] == ("", "")


# ── integration ──

def test_calendar_renders_date_links(window, qapp, tmp_path):
    fa = _folder(tmp_path, "work", ["alpha"])
    s = ProjectStore(fa); todo = s.add_todo("Task A"); s.save()
    window.load_folder(fa)
    window._build_entity_index()                     # resolve titles synchronously
    ref = make_ref("todo", str(fa), todo.id)
    window._link_store.add(ref, date_ref("2026-06-11"))
    window._refresh_calendar()
    grid_items = window.calendar_view.grid._items
    assert any(i.kind == "todo" and i.key == todo.id and i.start == "2026-06-11"
               for i in grid_items)                  # the linked todo shows on that day


# ── drag & drop ──

def test_compute_preview_move_and_resize(qapp):
    from projectum.widgets import MonthGrid
    g = MonthGrid()
    drag = {"mode": "move", "orig_start": date(2026, 6, 3),
            "orig_end": date(2026, 6, 5), "press_day": date(2026, 6, 3)}
    assert g._compute_preview(drag, date(2026, 6, 6)) == (date(2026, 6, 6), date(2026, 6, 8))
    drag["mode"] = "resize_end"
    assert g._compute_preview(drag, date(2026, 6, 10)) == (date(2026, 6, 3), date(2026, 6, 10))
    drag["mode"] = "resize_start"
    assert g._compute_preview(drag, date(2026, 6, 4)) == (date(2026, 6, 4), date(2026, 6, 5))
    # resize can't cross the opposite end
    assert g._compute_preview(drag, date(2026, 6, 9)) == (date(2026, 6, 5), date(2026, 6, 5))
    g.deleteLater()


def test_bar_drag_move_emits_reschedule(qapp):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF, Qt
    from projectum.widgets import MonthGrid
    g = MonthGrid()
    g.resize(900, 700)
    g.set_month(2026, 6)                       # Jun 1 is Monday -> Jun 3 is col 2
    g.set_items([_si("2026-06-03", "2026-06-03", title="X")])
    qapp.processEvents()
    got = []
    g.item_rescheduled.connect(lambda it, s, e: got.append((s, e)))

    bar = g._bars[0]
    press = g._bar_rect(bar).center()
    ox, _oy, cw, _ch = g._geom()
    target = QPointF(ox + 5 * cw + cw / 2, press.y())   # col 5 -> Jun 6 (+3 days)

    def ev(t, pos, buttons):
        return QMouseEvent(t, pos, pos, Qt.MouseButton.LeftButton, buttons,
                           Qt.KeyboardModifier.NoModifier)

    g.mousePressEvent(ev(QEvent.Type.MouseButtonPress, press, Qt.MouseButton.LeftButton))
    assert g._drag is not None and g._drag["mode"] == "move"
    g.mouseMoveEvent(ev(QEvent.Type.MouseMove, target, Qt.MouseButton.LeftButton))
    g.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, target, Qt.MouseButton.NoButton))
    assert got == [("2026-06-06", "2026-06-06")]
    g.deleteLater()


def test_calendar_day_attribute_opens_links_for_date(window, qapp, tmp_path):
    fa = _folder(tmp_path, "work", ["alpha"]); ProjectStore(fa).save()
    window.load_folder(fa)
    window._on_calendar_day_attribute(date(2026, 6, 15))   # "select a day and attribute it"
    qapp.processEvents()
    assert window._links_dialog is not None
    assert window._links_dialog._ref == date_ref("2026-06-15")   # links dialog for that date
    window._links_dialog.close()


def test_calendar_renders_frame_as_span(window, qapp, tmp_path):
    from projectum.links import daterange_ref
    fa = _folder(tmp_path, "work", ["alpha"])
    s = ProjectStore(fa); todo = s.add_todo("Sprint"); s.save()
    window.load_folder(fa); window._build_entity_index()
    window._link_store.add(make_ref("todo", str(fa), todo.id),
                           daterange_ref("2026-06-09", "2026-06-13"))
    window._refresh_calendar()
    e = next(i for i in window.calendar_view.grid._items if i.key == todo.id)
    assert e.start == "2026-06-09" and e.end == "2026-06-13"   # rendered as a span


def test_calendar_frame_attribute_opens_daterange_dialog(window, qapp, tmp_path):
    fa = _folder(tmp_path, "work", ["alpha"]); ProjectStore(fa).save()
    window.load_folder(fa)
    window._on_calendar_frame_attribute(date(2026, 6, 9), date(2026, 6, 13))
    qapp.processEvents()
    assert window._links_dialog._ref.kind == "daterange"
    assert window._links_dialog._ref.key == "2026-06-09..2026-06-13"
    window._links_dialog.close()


def test_monthgrid_hover_tracks_and_clears(qapp):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF, Qt
    from projectum.widgets import MonthGrid
    g = MonthGrid(); g.resize(800, 600); g.set_month(2026, 6)
    qapp.processEvents()
    ox, oy, cw, ch = g._geom()
    pos = QPointF(ox + cw / 2, oy + ch / 2)          # first cell
    g.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, pos, pos, Qt.MouseButton.NoButton,
                                 Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert g._hover_day == g._day_at(pos)            # hover tracked
    g.leaveEvent(QEvent(QEvent.Type.Leave))
    assert g._hover_day is None                      # cleared on leave (no ghosting)
    g.deleteLater()


def test_monthgrid_right_click_day_emits_day_context(qapp):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF, Qt
    from projectum.widgets import MonthGrid
    g = MonthGrid(); g.resize(800, 600); g.set_month(2026, 6)
    qapp.processEvents()
    ox, oy, cw, ch = g._geom()
    pos = QPointF(ox + cw / 2, oy + ch / 2)          # first day cell
    got = []
    g.day_context.connect(lambda d, _p: got.append(d))
    g.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier))
    assert got == [g._day_at(pos)] and g._day_at(pos) is not None
    assert g._day_sel is None                        # right-click starts no selection
    g.deleteLater()


def test_calendar_unlink_date_from_day(window, qapp, tmp_path):
    fa = _folder(tmp_path, "work", ["alpha"])
    s = ProjectStore(fa); todo = s.add_todo("Task A"); s.save()
    window.load_folder(fa)
    ref = make_ref("todo", str(fa), todo.id)
    window._link_store.add(ref, date_ref("2026-06-20"))
    assert window._link_store.has(ref, date_ref("2026-06-20"))
    window._unlink_date(ref, "2026-06-20")        # right-click "Remove from this day"
    assert not window._link_store.has(ref, date_ref("2026-06-20"))


def test_bar_click_without_drag_activates(qapp):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, Qt
    from projectum.widgets import MonthGrid
    g = MonthGrid()
    g.resize(900, 700)
    g.set_month(2026, 6)
    g.set_items([_si("2026-06-03", "2026-06-03", title="X")])
    qapp.processEvents()
    activated = []
    g.item_activated.connect(lambda it: activated.append(it))
    press = g._bar_rect(g._bars[0]).center()

    def ev(t, buttons):
        return QMouseEvent(t, press, press, Qt.MouseButton.LeftButton, buttons,
                           Qt.KeyboardModifier.NoModifier)

    g.mousePressEvent(ev(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton))
    g.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton))
    assert len(activated) == 1   # press+release without movement -> click (open picker)
    g.deleteLater()
