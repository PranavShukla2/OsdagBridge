"""
In-place editing of dimension labels on the 2D CAD views.

Both CAD widgets (cross-section and top view) paint their dimension text
themselves, so there is no real widget under a label that could take a
double-click. While painting, each widget records the rectangle of every
dimension label it draws together with the CAD parameter it belongs to.
On double-click we hit-test those rectangles and float a QLineEdit over the
label. The widget only reports the typed value through its
``dimension_value_edited(key, value)`` signal, already keyed by the input
dictionary key from common.py and in that key's units. Validating it and
writing it into the input dictionaries is left to the template page, which
owns that data.
"""

from PySide6.QtCore import Qt, QRectF, QEvent, QPoint, QLocale
from PySide6.QtGui import QDoubleValidator, QPen, QColor
from PySide6.QtWidgets import QLineEdit, QToolTip

from osdagbridge.core.utils.common import (
    KEY_SPAN, KEY_CARRIAGEWAY_WIDTH, KEY_TS_GIRDER_SPACING, KEY_TS_DECK_THICKNESS,
)

# CAD parameter -> (input dictionary key, unit, factor: CAD value (mm) -> key units)
# The CAD stores lengths in mm. Span, carriageway width and girder spacing are
# entered in metres (as in the input dock / Typical Section tab), deck
# thickness in mm (as in the Deck Details tab).
EDITABLE_CAD_PARAMS = {
    "span_length":       (KEY_SPAN,              "m",  1.0 / 1000.0),
    "carriageway_width": (KEY_CARRIAGEWAY_WIDTH, "m",  1.0 / 1000.0),
    "girder_spacing":    (KEY_TS_GIRDER_SPACING, "m",  1.0 / 1000.0),
    "deck_thickness":    (KEY_TS_DECK_THICKNESS, "mm", 1.0),
}

EDIT_HINT = "Double-click to edit"
LOCKED_LABEL_HINT = "{name} is derived from the other inputs and can't be edited here."
HOVER_COLOR = QColor(144, 175, 19)   # Osdag green


class _InlineLineEdit(QLineEdit):
    """QLineEdit that reports Escape instead of swallowing it."""

    def __init__(self, parent, on_cancel):
        super().__init__(parent)
        self._on_cancel = on_cancel

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


class EditableDimensionMixin:
    """
    Mixin for QWidget based CAD canvases.

    The host class must define ``dimension_value_edited = Signal(str, float)``
    and call:
        * ``self._init_label_editing()`` from ``__init__``
        * ``self._clear_label_regions()`` at the start of ``paintEvent``
        * ``self._register_label(rect, param)`` for each editable label
        * ``self._register_locked_label(rect, name)`` for read-only labels
        * ``self._paint_label_hover(painter)`` at the end of ``paintEvent``
        * ``self._update_label_hover(pos)`` from ``mouseMoveEvent``
    """

    def _init_label_editing(self):
        self.label_editing_enabled = False
        self._editable_label_regions = []   # [(QRectF, param)]
        self._locked_label_regions = []     # [(QRectF, display name)]
        self._label_editor = None
        self._editing_param = None
        self._hovered_label = None          # param or locked name under the mouse

    # -- registration (called while painting) ---------------------------------

    def _clear_label_regions(self):
        self._editable_label_regions = []
        self._locked_label_regions = []

    def _register_label(self, rect, param):
        if rect is not None and param in EDITABLE_CAD_PARAMS:
            self._editable_label_regions.append((QRectF(rect), param))

    def _register_locked_label(self, rect, name):
        if rect is not None:
            self._locked_label_regions.append((QRectF(rect), name))

    # -- hit testing ------------------------------------------------------------

    @staticmethod
    def _hit(rect, pos):
        # A little slack around the text makes small labels easier to hit.
        return rect.adjusted(-3, -3, 3, 3).contains(pos)

    @classmethod
    def _closest_hit(cls, regions, pos):
        # Labels can overlap when the canvas is small; take the one whose
        # centre is nearest to the click rather than whichever was drawn first.
        hits = [(r, v) for r, v in regions if cls._hit(r, pos)]
        if not hits:
            return None
        def dist2(r):
            c = r.center()
            return (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2
        return min(hits, key=lambda h: dist2(h[0]))[1]

    def _label_at(self, pos):
        return self._closest_hit(self._editable_label_regions, pos)

    def _locked_label_at(self, pos):
        return self._closest_hit(self._locked_label_regions, pos)

    def _rect_for_param(self, param):
        for rect, p in self._editable_label_regions:
            if p == param:
                return rect
        return None

    # -- hover feedback -----------------------------------------------------------

    def _update_label_hover(self, pos):
        """Pointer cursor, tooltip and highlight while over an editable label."""
        if not self.label_editing_enabled or self._label_editor is not None:
            return
        param = self._label_at(pos)
        hovered = param or self._locked_label_at(pos)
        if hovered == self._hovered_label:
            return
        self._hovered_label = hovered
        if param is not None:
            self.setCursor(Qt.PointingHandCursor)
            QToolTip.showText(self.mapToGlobal(QPoint(int(pos.x()), int(pos.y()) + 16)), EDIT_HINT, self)
        else:
            self.unsetCursor()
            QToolTip.hideText()
        self.update()

    def leaveEvent(self, event):
        if getattr(self, "_hovered_label", None) is not None:
            self._hovered_label = None
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def _paint_label_hover(self, painter):
        if not self.label_editing_enabled or self._label_editor is not None:
            return
        rect = self._rect_for_param(self._hovered_label) if self._hovered_label else None
        if rect is None:
            return
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(HOVER_COLOR, 1.2))
        painter.drawRoundedRect(rect.adjusted(-3, -2, 3, 2), 3, 3)
        painter.restore()

    # -- Qt event --------------------------------------------------------------

    def mouseDoubleClickEvent(self, event):
        if not self.label_editing_enabled or event.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(event)

        pos = event.position() if hasattr(event, "position") else event.pos()

        param = self._label_at(pos)
        if param is not None:
            self._open_label_editor(param)
            event.accept()
            return

        locked = self._locked_label_at(pos)
        if locked is not None:
            QToolTip.showText(
                self.mapToGlobal(QPoint(int(pos.x()), int(pos.y()))),
                LOCKED_LABEL_HINT.format(name=locked),
                self,
            )
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

    # -- editor lifecycle ----------------------------------------------------

    def _current_display_value(self, param):
        _, unit, factor = EDITABLE_CAD_PARAMS[param]
        value = self.params.get(param)
        if value is None:
            return ""
        shown = float(value) * factor
        return f"{shown:.0f}" if unit == "mm" else f"{shown:.2f}"

    def _open_label_editor(self, param):
        rect = self._rect_for_param(param)
        if rect is None:
            return
        if self._label_editor is not None:
            self._close_label_editor(commit=False)
        self._hovered_label = None
        self.unsetCursor()
        QToolTip.hideText()

        editor = _InlineLineEdit(self, on_cancel=lambda: self._close_label_editor(commit=False))
        validator = QDoubleValidator(0.0, 1e6, 3, editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setLocale(QLocale.c())   # always '.' as decimal point
        editor.setValidator(validator)
        editor.setText(self._current_display_value(param))
        editor.setAlignment(Qt.AlignCenter)
        editor.setToolTip(f"Value in {EDITABLE_CAD_PARAMS[param][1]} "
                          "(Enter to apply, Esc to cancel)")
        editor.setStyleSheet(
            "QLineEdit { background: #ffffff; border: 1px solid #90AF13;"
            " border-radius: 3px; padding: 0px 3px; font: 9pt 'Arial'; }"
        )

        width = max(80, int(rect.width()) + 10)
        height = max(22, int(rect.height()) + 4)
        x = int(rect.center().x() - width / 2)
        y = int(rect.center().y() - height / 2)
        editor.setGeometry(x, y, width, height)

        # Enter commits, focus-out (clicking elsewhere) commits too.
        editor.returnPressed.connect(lambda: self._close_label_editor(commit=True))
        editor.installEventFilter(self)

        self._label_editor = editor
        self._editing_param = param
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)
        editor.selectAll()
        self.update()

    def eventFilter(self, obj, event):
        editor = getattr(self, "_label_editor", None)
        if editor is not None and obj is editor and event.type() == QEvent.FocusOut:
            self._close_label_editor(commit=True)
            return False
        return super().eventFilter(obj, event)

    def _close_label_editor(self, commit):
        editor, param = self._label_editor, self._editing_param
        if editor is None:
            return
        # Clear first: hiding the editor triggers FocusOut, which would call us again.
        self._label_editor = None
        self._editing_param = None

        text = editor.text().strip()
        editor.removeEventFilter(self)
        editor.hide()
        editor.deleteLater()
        self.update()

        if not commit or not text or text == self._current_display_value(param):
            return
        try:
            value = float(text)
        except ValueError:
            return
        key = EDITABLE_CAD_PARAMS[param][0]
        self.dimension_value_edited.emit(key, value)
