"""
Rules for "Custom" selections in the Additional Inputs dialog.

Everything here is read from ADDITIONAL_INPUTS_SCHEMA, so the save-time check
needs no parameter names written out by hand. A rule says: when the
controller widget is set to VALUE_CUSTOM, these value widgets must not be
empty. Three schema patterns produce rules:

* TYPE_MODE_LINE fields - a mode combo (<id>.mode) next to a value box
  (<id>.value).
* TYPE_COMBOBOX fields with a "custom_inputs" list - picking Custom unlocks
  the listed text fields (e.g. crash barrier width/height).
* TYPE_ADAPTIVE fields whose Custom mode is a text box - their controller is
  a key in the working dictionary (the design mode), not a widget.
"""

import html
import re
from dataclasses import dataclass
from functools import lru_cache

from osdagbridge.core.utils.common import (
    TYPE_ADAPTIVE, TYPE_COMBOBOX, TYPE_MODE_LINE, TYPE_TEXTBOX,
    VALUE_CUSTOM, MODE_LINE_MODE_SUFFIX, MODE_LINE_VALUE_SUFFIX,
)

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class CustomRule:
    controller: str        # objectName of the combo, or working-dict key for adaptive fields
    value_widget: str      # objectName of the input that must be filled
    display_name: str      # shown in the error message
    adaptive: bool = False


def clean_label(text: str) -> str:
    """'Drag Coefficient, C<sub>D</sub>' -> 'Drag Coefficient, CD'; drops a trailing colon."""
    text = html.unescape(_TAG_RE.sub("", str(text or "")))
    return " ".join(text.split()).rstrip(":").strip()


def _is_field(node) -> bool:
    return isinstance(node, dict) and "type" in node and "id" in node


def _context_title(node, current: str) -> str:
    # Sub-tab / section / card headings that are not field labels themselves
    if _is_field(node):
        return current
    for key in ("title", "label"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return clean_label(value)
    return current


def _walk(node, context, fields, out):
    if isinstance(node, list):
        for item in node:
            _walk(item, context, fields, out)
        return
    if not isinstance(node, dict):
        return

    context = _context_title(node, context)
    if _is_field(node):
        fields[node["id"]] = (node, context)
        out.append(node["id"])

    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, context, fields, out)


def _name(context: str, label: str) -> str:
    label = clean_label(label)
    if context and context.lower() not in label.lower():
        return f"{context} - {label}" if label else context
    return label or context


@lru_cache(maxsize=None)
def _rules_for(schema_ref, tab_label: str):
    fields, order = {}, []
    _walk(schema_ref.obj, tab_label, fields, order)

    rules = []
    for field_id in order:
        field, context = fields[field_id]
        ftype = field.get("type")

        if ftype == TYPE_MODE_LINE and VALUE_CUSTOM in (field.get("mode_choices") or []):
            rules.append(CustomRule(
                controller=field_id + MODE_LINE_MODE_SUFFIX,
                value_widget=field_id + MODE_LINE_VALUE_SUFFIX,
                display_name=_name(context, field.get("label", "")),
            ))

        elif ftype == TYPE_COMBOBOX and field.get("custom_inputs"):
            for dep_id in field["custom_inputs"]:
                dep, dep_context = fields.get(dep_id, ({}, context))
                rules.append(CustomRule(
                    controller=field_id,
                    value_widget=dep_id,
                    display_name=_name(dep_context, dep.get("label", dep_id)),
                ))

        elif ftype == TYPE_ADAPTIVE:
            custom_mode = (field.get("modes") or {}).get(VALUE_CUSTOM)
            if custom_mode and custom_mode.get("type") == TYPE_TEXTBOX and field.get("controller"):
                rules.append(CustomRule(
                    controller=field["controller"],
                    value_widget=field_id,
                    display_name=_name(context, field.get("label", "")),
                    adaptive=True,
                ))
    return tuple(rules)


class _Hashable:
    """lru_cache needs hashable args; schemas are dicts, so key on identity."""
    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __hash__(self):
        return id(self.obj)

    def __eq__(self, other):
        return isinstance(other, _Hashable) and other.obj is self.obj


def custom_rules(schema: dict, tab_label: str):
    """All Custom rules for one top-level Additional Inputs tab (cached)."""
    return _rules_for(_Hashable(schema), tab_label)
