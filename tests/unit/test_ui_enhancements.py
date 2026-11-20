"""
CAD label editing (Task I), Custom-value rules (Task II) and the girder-spacing
layout helper. Qt parts run on the offscreen platform.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from osdagbridge.core.utils.common import (
    KEY_CARRIAGEWAY_WIDTH, KEY_CB_TYPE, KEY_CB_WIDTH, KEY_DESIGN_MODE,
    KEY_FOOTPATH, KEY_INCLUDE_MEDIAN, KEY_MD_WIDTH, KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS, KEY_TS_GIRDER_SPACING, KEY_TS_NO_OF_GIRDERS,
    KEY_WL_GUST_FACTOR, MODE_LINE_MODE_SUFFIX, MODE_LINE_VALUE_SUFFIX,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ── Task II: rules come from the schema ──────────────────────────────────────

def test_custom_rules_are_read_from_schema(app):
    from osdagbridge.core.bridge_types.plate_girder.ui_fields_additional_input import ADDITIONAL_INPUTS_SCHEMA
    from osdagbridge.desktop.ui.dialogs.additional_input.custom_input_rules import custom_rules

    rules = [r for t in ADDITIONAL_INPUTS_SCHEMA for r in custom_rules(t["schema"], t["label"])]
    by_value = {r.value_widget: r for r in rules}

    cb = by_value[KEY_CB_WIDTH]
    assert cb.controller == KEY_CB_TYPE and cb.display_name == "Crash Barrier - Width (m)"

    gust = by_value[KEY_WL_GUST_FACTOR + MODE_LINE_VALUE_SUFFIX]
    assert gust.controller == KEY_WL_GUST_FACTOR + MODE_LINE_MODE_SUFFIX
    assert "<" not in gust.display_name          # html like <sub> stripped

    assert any(r.adaptive and r.controller == KEY_DESIGN_MODE for r in rules)


def test_clean_label():
    from osdagbridge.desktop.ui.dialogs.additional_input.custom_input_rules import clean_label
    assert clean_label("Drag Coefficient, C<sub>D</sub>:") == "Drag Coefficient, CD"
    assert clean_label("Concrete, &#947;<sub>c</sub>") == "Concrete, γc"


# ── Task I: girder spacing re-solve ──────────────────────────────────────────

def _layout_inputs():
    return {
        KEY_CARRIAGEWAY_WIDTH: "7.5", KEY_CB_WIDTH: 0.45, KEY_FOOTPATH: "None",
        KEY_INCLUDE_MEDIAN: "No", KEY_MD_WIDTH: None, KEY_TS_NO_OF_GIRDERS: 4,
    }


def test_apply_girder_spacing_keeps_girder_count():
    from osdagbridge.core.bridge_types.plate_girder.defaults import apply_girder_spacing
    d = _layout_inputs()
    apply_girder_spacing(d, 2.4)
    assert d[KEY_TS_NO_OF_GIRDERS] == 4
    assert d[KEY_TS_GIRDER_SPACING] == pytest.approx(2.4)
    # 8.4 m deck: 3 x 2.4 + 2 x overhang
    assert d[KEY_TS_DECK_OVERHANG] == pytest.approx(0.6)


def test_apply_girder_spacing_rejects_impossible_layout():
    from osdagbridge.core.bridge_types.plate_girder.defaults import apply_girder_spacing
    d = _layout_inputs()
    with pytest.raises(ValueError):
        apply_girder_spacing(d, 4.0)               # 3 x 4.0 > 8.4 m
    assert KEY_TS_GIRDER_SPACING not in d          # untouched on failure


# ── Task I: label editing on the CAD widget ──────────────────────────────────

def _painted_cross_section(app):
    from osdagbridge.desktop.ui.docks.cad_cross_section import CrossSectionCADWidget
    w = CrossSectionCADWidget()
    w.label_editing_enabled = True
    w.resize(1100, 700)
    w.show()
    w.grab()                                      # paint once so label rects exist
    return w


def _double_click_label(w, param):
    from PySide6.QtTest import QTest
    rect = next(r for r, p in w._editable_label_regions if p == param)
    QTest.mouseDClick(w, Qt.LeftButton, Qt.NoModifier, rect.center().toPoint())
    return w._label_editor


def test_double_click_edit_emits_dictionary_key(app):
    from PySide6.QtTest import QTest
    w = _painted_cross_section(app)
    assert {p for _, p in w._editable_label_regions} >= {"carriageway_width", "girder_spacing", "deck_thickness"}

    got = []
    w.dimension_value_edited.connect(lambda k, v: got.append((k, v)))
    editor = _double_click_label(w, "deck_thickness")
    assert editor is not None and editor.text() == "200"
    editor.selectAll()
    QTest.keyClicks(editor, "230")
    QTest.keyClick(editor, Qt.Key_Return)

    assert got == [(KEY_TS_DECK_THICKNESS, 230.0)]
    assert w._label_editor is None


def test_escape_cancels_and_locked_labels_do_not_open(app):
    from PySide6.QtTest import QTest
    w = _painted_cross_section(app)
    got = []
    w.dimension_value_edited.connect(lambda k, v: got.append((k, v)))

    editor = _double_click_label(w, "girder_spacing")
    QTest.keyClicks(editor, "9")
    QTest.keyClick(editor, Qt.Key_Escape)
    assert got == [] and w._label_editor is None

    w.grab()
    rect = next(r for r, name in w._locked_label_regions if name == "Overhang")
    QTest.mouseDClick(w, Qt.LeftButton, Qt.NoModifier, rect.center().toPoint())
    assert w._label_editor is None


def test_editing_disabled_by_default(app):
    """The Additional Inputs preview uses the same widget and must stay read-only."""
    from PySide6.QtTest import QTest
    from osdagbridge.desktop.ui.docks.cad_cross_section import CrossSectionCADWidget
    w = CrossSectionCADWidget()
    w.resize(1100, 700); w.show(); w.grab()
    rect = next(r for r, p in w._editable_label_regions if p == "girder_spacing")
    QTest.mouseDClick(w, Qt.LeftButton, Qt.NoModifier, rect.center().toPoint())
    assert w._label_editor is None
