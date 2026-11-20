"""
In-place editing of dimension labels on the 2D CAD views.

Both CAD widgets (cross-section and top view) paint their dimension text
themselves, so there is no real widget under a label that could take a
double-click. While painting, each widget records the rectangle of every
dimension label it draws together with the CAD parameter it belongs to.
On double-click we hit-test those rectangles and float a QLineEdit over the
label. The widget only reports the typed value through its
``dimension_value_edited`` signal; validating it and writing it into the
input dictionaries is left to the template page, which owns that data.
"""

from PySide6.QtCore import Qt, QRectF, QEvent, QPoint
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QLineEdit, QToolTip


# CAD parameter -> (unit shown to the user, factor: CAD value (mm) -> shown value)
# CAD params are stored in mm internally; span/width/spacing are typed in metres
# (same as the input dock) and deck thickness in mm (same as Additional Inputs).
EDITABLE_CAD_PARAMS = {
    "span_length":       ("m",  1.0 / 1000.0),
    "carriageway_width": ("m",  1.0 / 1000.0),
    "girder_spacing":    ("m",  1.0 / 1000.0),
    "deck_thickness":    ("mm", 1.0),
}

LOCKED_LABEL_HINT = (
    "{name} is derived from the other inputs and can't be edited here."
)


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
    """

    def _init_label_editing(self):
        self.label_editing_enabled = False
        self._editable_label_regions = []   # [(QRectF, param)]
        self._locked_label_regions = []     # [(QRectF, display name)]
        self._label_editor = None
        self._editing_param = None

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

    def _label_at(self, pos):
        # A little slack around the text makes small labels easier to hit.
        for rect, param in self._editable_label_regions:
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return param
        return None

    def _locked_label_at(self, pos):
        for rect, name in self._locked_label_regions:
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return name
        return None

    def _rect_for_param(self, param):
        for rect, p in self._editable_label_regions:
            if p == param:
                return rect
        return None

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
        unit, factor = EDITABLE_CAD_PARAMS[param]
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

        editor = _InlineLineEdit(self, on_cancel=lambda: self._close_label_editor(commit=False))
        validator = QDoubleValidator(0.0, 1e6, 3, editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        editor.setValidator(validator)
        editor.setText(self._current_display_value(param))
        editor.setAlignment(Qt.AlignCenter)
        editor.setToolTip(f"Enter value in {EDITABLE_CAD_PARAMS[param][0]} "
                          "(Enter to apply, Esc to cancel)")
        editor.setStyleSheet(
            "QLineEdit { background: #ffffff; border: 1px solid #90AF13;"
            " border-radius: 3px; padding: 0px 3px; font: 9pt 'Arial'; }"
        )

        width = max(70, int(rect.width()) + 10)
        height = max(20, int(rect.height()) + 4)
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

        if not commit or not text or text == self._current_display_value(param):
            return
        try:
            value = float(text)
        except ValueError:
            return
        self.dimension_value_edited.emit(param, value)
