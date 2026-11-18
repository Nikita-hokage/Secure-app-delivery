"""Parse a declarative description of the rows a recovery drill must observe."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


class DrillError(ValueError):
    """A drill input is invalid or its result is inconclusive."""


ROLES = ("erase", "same_tenant", "other_tenant")


@dataclass(frozen=True)
class Probe:
    table: str
    tenant_column: str
    subject_column: str


@dataclass(frozen=True)
class Contract:
    actors: dict
    probes: tuple[Probe, ...]


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DrillError(f"duplicate contract field: {key!r}")
        result[key] = value
    return result


def keys(value, expected, where):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise DrillError(f"{where} requires exactly these fields: {', '.join(expected)}")


def parse(data):
    keys(data, ("version", "actors", "tables"), "contract")
    if type(data["version"]) is not int or data["version"] != 1:
        raise DrillError("unsupported contract version")
    keys(data["actors"], ROLES, "actors")
    actors = {}
    for role in ROLES:
        pair = data["actors"][role]
        keys(pair, ("tenant", "subject"), role)
        values = (pair["tenant"], pair["subject"])
        for value in values:
            if type(value) is int and -(2**63) <= value < 2**63:
                continue
            if type(value) is not str or not value:
                raise DrillError(f"{role}: IDs must be nonempty text or signed 64-bit integers")
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise DrillError(f"{role}: invalid text ID") from exc
        actors[role] = values
    target, sibling, twin = (actors[role] for role in ROLES)
    for column in (0, 1):
        if len({type(pair[column]) for pair in actors.values()}) != 1:
            raise DrillError("use one storage type for each ID column")
    if not (sibling[0] == target[0] and sibling[1] != target[1]
            and twin[0] != target[0] and twin[1] == target[1]):
        raise DrillError("same_tenant must share only the tenant; other_tenant must share only the subject")
    if not isinstance(data["tables"], list) or not data["tables"]:
        raise DrillError("declare at least one table")
    probes = []
    names = set()
    for entry in data["tables"]:
        keys(entry, ("table", "tenant_column", "subject_column"), "table probe")
        for value in entry.values():
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise DrillError("table and column names must be simple SQL identifiers")
        probe = Probe(**entry)
        if probe.table.lower() in names or probe.tenant_column.lower() == probe.subject_column.lower():
            raise DrillError("duplicate table or identity column")
        names.add(probe.table.lower())
        probes.append(probe)
    return Contract(actors, tuple(probes))


def load(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise DrillError("contract exceeds 256 KiB")
        return parse(json.loads(raw.decode("utf-8"), object_pairs_hook=unique))
    except (OSError, UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise DrillError("cannot read contract as UTF-8 JSON") from exc
