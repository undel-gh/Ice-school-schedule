from __future__ import annotations

import ast
import re
from pathlib import Path

from django.conf import settings

from core.presentation import localize_message

ERROR_NAMES = {"ValidationError", "PermissionDenied"}
IGNORED_PARTS = {"migrations", "tests", "management", "__pycache__"}
LATIN_RE = re.compile(r"[A-Za-z]")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

# Dynamic messages cannot be looked up by exact string. Keeping their normalized
# AST template together with one concrete example makes adding a new f-string
# error an explicit localization decision.
DYNAMIC_ERROR_EXAMPLES = {
    'Cannot reactivate student: group "{}" has no free place for this membership. End or move the membership first.': (
        'Cannot reactivate student: group "Beginners" has no free place for this membership. End or move the membership first.'
    ),
    "External identity provider rejected the request ({}).": (
        "External identity provider rejected the request (access_denied)."
    ),
    "Unsupported attendance transition: {} -> {}.": (
        "Unsupported attendance transition: present -> absent."
    ),
    "{} active roster participant(s) remain unmarked.": (
        "2 active roster participant(s) remain unmarked."
    ),
    "Medical make-up expiry must be on or after {}.": (
        "Medical make-up expiry must be on or after 2026-10-01."
    ),
    "Group capacity of {} would be exceeded on {}.": (
        "Group capacity of 12 would be exceeded on 2026-10-01."
    ),
    "Capacity cannot be lower than existing current or future seat claims (first conflict: {}).": (
        "Capacity cannot be lower than existing current or future seat claims (first conflict: 2026-10-01)."
    ),
    "Template versioning would affect a published or processed lesson. Reschedule/cancel that lesson explicitly first: {}.": (
        "Template versioning would affect a published or processed lesson. Reschedule/cancel that lesson explicitly first: 11111111-1111-1111-1111-111111111111."
    ),
    "Template versioning would cancel a DRAFT lesson with an active enrollment or one-time entitlement. Use the reschedule_lesson command to move that booked lesson to an explicit exception slot outside the new recurring template slot first: {}.": (
        "Template versioning would cancel a DRAFT lesson with an active enrollment or one-time entitlement. Use the reschedule_lesson command to move that booked lesson to an explicit exception slot outside the new recurring template slot first: 11111111-1111-1111-1111-111111111111."
    ),
    "Template occurrence is already materialized as lesson {} with status {}.": (
        "Template occurrence is already materialized as lesson 11111111-1111-1111-1111-111111111111 with status rsvp_open."
    ),
    "The selected coach has another non-cancelled lesson overlapping this time: {}.": (
        "The selected coach has another non-cancelled lesson overlapping this time: 11111111-1111-1111-1111-111111111111."
    ),
    "Replacement interval overlaps another non-cancelled lesson of this group: {}.": (
        "Replacement interval overlaps another non-cancelled lesson of this group: 11111111-1111-1111-1111-111111111111."
    ),
    "Unsupported allowance categories: {}.": (
        "Unsupported allowance categories: ['unknown']."
    ),
    "Multiple active absence compensation policies match {} on {}.": (
        "Multiple active absence compensation policies match 'unexcused' on 2026-10-01."
    ),
    "Multiple absence compensation windows with the same priority match action {} on {}.": (
        "Multiple absence compensation windows with the same priority match action 11111111-1111-1111-1111-111111111111 on 2026-10-01."
    ),
    "Multiple subscriptions match the next student period starting/issued on {}. Resolve the duplicate/overlap before issuing compensation.": (
        "Multiple subscriptions match the next student period starting/issued on 2026-10-01. Resolve the duplicate/overlap before issuing compensation."
    ),
}


def _production_python_files() -> list[Path]:
    root = Path(settings.BASE_DIR)
    files = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        files.append(path)
    return sorted(files)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _looks_like_english_message(value: str) -> bool:
    if not LATIN_RE.search(value) or CYRILLIC_RE.search(value):
        return False
    return bool(re.search(r"\s", value)) and bool(re.search(r"[.!?:]", value))


def _joined_template(node: ast.JoinedStr) -> str:
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append("{}")
    return "".join(parts)


def _error_messages_from_ast(path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    static_messages: set[str] = set()
    dynamic_templates: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in ERROR_NAMES:
            continue

        joined_nodes = [
            child for child in ast.walk(node) if isinstance(child, ast.JoinedStr)
        ]
        joined_constant_ids = {
            id(child)
            for joined in joined_nodes
            for child in ast.walk(joined)
            if isinstance(child, ast.Constant)
        }

        for joined in joined_nodes:
            template = _joined_template(joined)
            if _looks_like_english_message(template.replace("{}", "value")):
                dynamic_templates.add(template)

        for child in ast.walk(node):
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and id(child) not in joined_constant_ids
                and _looks_like_english_message(child.value)
            ):
                static_messages.add(child.value)

    return static_messages, dynamic_templates


def test_all_static_english_domain_errors_have_ui_translation():
    missing: dict[str, list[str]] = {}

    for path in _production_python_files():
        static_messages, _ = _error_messages_from_ast(path)
        untranslated = sorted(
            message
            for message in static_messages
            if localize_message(message) == message
        )
        if untranslated:
            missing[str(path.relative_to(settings.BASE_DIR))] = untranslated

    assert missing == {}


def test_dynamic_domain_errors_are_explicitly_covered():
    discovered: set[str] = set()
    for path in _production_python_files():
        _, dynamic_templates = _error_messages_from_ast(path)
        discovered.update(dynamic_templates)

    assert discovered == set(DYNAMIC_ERROR_EXAMPLES)

    for template, example in DYNAMIC_ERROR_EXAMPLES.items():
        translated = localize_message(example)
        assert translated != example, template


def test_dynamic_ui_errors_hide_known_technical_values():
    attendance = localize_message(
        "Unsupported attendance transition: present -> absent."
    )
    occurrence = localize_message(
        "Template occurrence is already materialized as lesson "
        "11111111-1111-1111-1111-111111111111 with status rsvp_open."
    )
    dated = localize_message(
        "Medical make-up expiry must be on or after 2026-10-01."
    )
    conflict = localize_message(
        "The selected coach has another non-cancelled lesson overlapping this time: "
        "11111111-1111-1111-1111-111111111111."
    )
    compensation = localize_message(
        "Multiple active absence compensation policies match 'unexcused' on "
        "2026-10-01."
    )
    action_window = localize_message(
        "Multiple absence compensation windows with the same priority match action "
        "11111111-1111-1111-1111-111111111111 on 2026-10-01."
    )

    assert "present" not in attendance
    assert "absent" not in attendance
    assert "rsvp_open" not in occurrence
    assert "11111111-1111-1111-1111-111111111111" not in occurrence
    assert "2026-10-01" not in dated
    assert "01.10.2026" in dated
    assert "11111111-1111-1111-1111-111111111111" not in conflict
    assert "unexcused" not in compensation
    assert "2026-10-01" not in compensation
    assert "01.10.2026" in compensation
    assert "11111111-1111-1111-1111-111111111111" not in action_window
    assert "01.10.2026" in action_window
