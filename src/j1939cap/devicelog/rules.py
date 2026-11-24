r"""Where the values of an extract stand in a log.

`schema` says what an extract may hold. Where each value stands in the text depends on the firmware that wrote the
log, and that wording is not published with this repository. It lives in a rules file kept next to the
de-identification key: a JSON document of regular expressions.

    {
      "line": "^(?P<time>\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}) (?P<body>.*)$",
      "time_format": "%Y-%m-%d %H:%M:%S",
      "serial": "unit (?P<serial>[A-Z]{2}\\d{6})",
      "cycle_start": "^reading cycle",
      "missing": ["N/A", ""],
      "fields": [{"column": "speed_kmh", "pattern": "^\\[(?P<state>\\w+)\\] speed=(?P<value>.*)$"}],
      "events": [{"event": "capture_requested", "pattern": "^capture asked for (?P<seconds>\\d+) s"}]
    }

Every log line is split by `line` into a time (UTC) and a body, and the other patterns are searched in the body.
A field rule fills one reading column from its `value` group, and may name the device state with a `state` group.
An event rule names one event of the schema; its named groups are the values of that event. A rule that asks for a
column, an event or a value the schema does not have is refused when the file is loaded, so a rules file can only
choose among what the schema allows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import schema

RULES_FILE_VARIABLE = "J1939CAP_LOG_RULES_FILE"


class RulesError(Exception):
    """The rules file asks for something an extract may not hold, or is not a rules file."""


@dataclass(frozen=True)
class FieldRule:
    column: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class EventRule:
    event: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Rules:
    line: re.Pattern[str]
    time_format: str
    serial: re.Pattern[str]
    cycle_start: re.Pattern[str]
    missing: frozenset[str]
    fields: tuple[FieldRule, ...]
    events: tuple[EventRule, ...]
    any_rule: re.Pattern[str]  # one search that tells whether a body is worth a closer look


def _compile(text: object, what: str) -> re.Pattern[str]:
    if not isinstance(text, str) or not text:
        raise RulesError(f"{what}: expected a regular expression")
    try:
        return re.compile(text)
    except re.error as error:
        raise RulesError(f"{what}: {error}") from None


def _groups(pattern: re.Pattern[str], needed: set[str], allowed: set[str], what: str) -> None:
    names = set(pattern.groupindex)
    if not needed <= names:
        raise RulesError(f"{what}: needs the groups {sorted(needed)}")
    if not names <= allowed:
        raise RulesError(f"{what}: {sorted(names - allowed)} is not part of the schema")


def parse(document: object) -> Rules:
    if not isinstance(document, dict):
        raise RulesError("a rules file is a JSON object")
    line = _compile(document.get("line"), "line")
    _groups(line, {"time", "body"}, {"time", "body", "level"}, "line")
    serial = _compile(document.get("serial"), "serial")
    _groups(serial, {"serial"}, {"serial"}, "serial")
    cycle_start = _compile(document.get("cycle_start"), "cycle_start")
    time_format = document.get("time_format")
    if not isinstance(time_format, str) or not time_format:
        raise RulesError("time_format: expected a strptime format")
    missing = document.get("missing", [])
    if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
        raise RulesError("missing: expected a list of strings")

    fields = []
    for n, item in enumerate(document.get("fields", [])):
        what = f"fields[{n}]"
        column = item.get("column") if isinstance(item, dict) else None
        if column not in schema.READING_TYPES or column == "time":
            raise RulesError(f"{what}: {column!r} is not a reading column")
        pattern = _compile(item.get("pattern"), what)
        needed = set() if schema.READING_TYPES[column] == schema.FLAG else {"value"}
        _groups(pattern, needed, needed | {"state"}, what)
        fields.append(FieldRule(column, pattern))

    events = []
    for n, item in enumerate(document.get("events", [])):
        what = f"events[{n}]"
        event = item.get("event") if isinstance(item, dict) else None
        if event not in schema.EVENTS:
            raise RulesError(f"{what}: {event!r} is not an event of the schema")
        pattern = _compile(item.get("pattern"), what)
        _groups(pattern, set(), set(schema.EVENTS[event]), what)
        events.append(EventRule(event, pattern))

    # The same patterns as one alternation, group names made unique, to pass over the bulk of a log quickly.
    sources = [cycle_start.pattern, *(rule.pattern.pattern for rule in fields), *(rule.pattern.pattern for rule in events)]
    union = "|".join(f"(?:{source.replace('(?P<', f'(?P<r{n}_')})" for n, source in enumerate(sources))
    return Rules(line, time_format, serial, cycle_start, frozenset(missing), tuple(fields), tuple(events), _compile(union, "rules"))


def load(path: str | Path) -> Rules:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RulesError(f"{path}: {error}") from None
    return parse(document)
