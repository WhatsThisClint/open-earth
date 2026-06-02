from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .models import normalize_slug
from .review_queue import ReviewQueue


@dataclass(frozen=True)
class TriggerRule:
    id: str
    name: str
    description: str
    source: str
    metric: str
    severity: str = "medium"
    confidence: str = "medium"
    condition: dict[str, Any] = field(default_factory=dict)
    conditions: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TriggerMatch:
    rule: TriggerRule
    event: dict[str, Any]
    observed_value: Any
    review_item_id: str | None = None


class TriggerEngine:
    """Evaluates live/static data events against local trigger rules."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.triggers_dir = self.root / "triggers"

    def rules(self) -> list[TriggerRule]:
        rules: list[TriggerRule] = []
        for path in sorted(self.triggers_dir.glob("*.trigger.yaml")):
            data = _read_yaml(path)
            for raw in data.get("rules") or []:
                if isinstance(raw, dict):
                    rules.append(_rule_from_mapping(path, raw))
        return rules

    def evaluate(self, event: dict[str, Any], *, queue: bool = True) -> list[TriggerMatch]:
        event = _normalise_event(event)
        matches: list[TriggerMatch] = []
        review_queue = ReviewQueue(self.root) if queue else None
        for rule in self.rules():
            if rule.source and event.get("source") and normalize_slug(rule.source) != normalize_slug(str(event["source"])):
                continue
            observed = _value_for_rule(event, rule)
            conditions = rule.conditions or [rule.condition]
            if not conditions or all(_condition_passes(event, rule, condition) for condition in conditions):
                review_item_id = None
                if review_queue and bool(rule.review.get("required", True)):
                    review_item = review_queue.add_item(
                        title=rule.name,
                        severity=rule.severity,
                        item_type="trigger",
                        source=str(event.get("source") or rule.source),
                        payload={
                            "matched_at": _now(),
                            "event": event,
                            "rule": asdict(rule),
                            "observed_value": observed,
                        },
                        recommendation={
                            "workflow": rule.review.get("workflow"),
                            "agents": rule.review.get("agents") or [],
                            "suggested_action": rule.review.get("suggested_action") or rule.description,
                            "field_validation": rule.review.get("field_validation") or [],
                        },
                    )
                    review_item_id = review_item.id
                matches.append(TriggerMatch(rule=rule, event=event, observed_value=observed, review_item_id=review_item_id))
        return matches


def load_event(path: str | Path) -> dict[str, Any]:
    event_path = Path(path)
    text = event_path.read_text(encoding="utf-8")
    if event_path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{event_path}: expected event object")
    return data


def _rule_from_mapping(path: Path, data: dict[str, Any]) -> TriggerRule:
    rule_id = normalize_slug(str(data.get("id") or data.get("name") or path.stem))
    condition = data.get("condition") or {}
    conditions = data.get("conditions") or []
    return TriggerRule(
        id=rule_id,
        name=str(data.get("name") or rule_id.replace("_", " ").title()),
        description=str(data.get("description") or ""),
        source=str(data.get("source") or ""),
        metric=str(data.get("metric") or condition.get("metric") or condition.get("field") or ""),
        severity=str(data.get("severity") or "medium"),
        confidence=str(data.get("confidence") or "medium"),
        condition=condition if isinstance(condition, dict) else {},
        conditions=conditions if isinstance(conditions, list) else [],
        review=data.get("review") if isinstance(data.get("review"), dict) else {},
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )


def _condition_passes(event: dict[str, Any], rule: TriggerRule, condition: dict[str, Any]) -> bool:
    metric = str(condition.get("metric") or condition.get("field") or rule.metric)
    op = str(condition.get("op") or condition.get("operator") or ">=").lower()
    expected = condition.get("value")
    actual = _lookup(event, metric)
    if actual is None and metric == rule.metric and "value" in event:
        actual = event.get("value")
    if op in {"present", "exists"}:
        return actual is not None
    if op in {"missing", "absent"}:
        return actual is None
    if actual is None:
        return False
    if op in {"contains", "includes"}:
        if isinstance(actual, list):
            return expected in actual
        return str(expected).lower() in str(actual).lower()
    if op in {"==", "=", "eq"}:
        return _coerce(actual) == _coerce(expected)
    if op in {"!=", "ne"}:
        return _coerce(actual) != _coerce(expected)
    left = _as_float(actual)
    right = _as_float(expected)
    if left is None or right is None:
        return False
    if op in {">", "gt"}:
        return left > right
    if op in {">=", "gte"}:
        return left >= right
    if op in {"<", "lt"}:
        return left < right
    if op in {"<=", "lte"}:
        return left <= right
    raise ValueError(f"unsupported trigger operator: {op}")


def _value_for_rule(event: dict[str, Any], rule: TriggerRule) -> Any:
    value = _lookup(event, rule.metric) if rule.metric else None
    if value is None:
        value = event.get("value")
    return value


def _lookup(event: dict[str, Any], path: str) -> Any:
    if not path:
        return None
    current: Any = event
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _normalise_event(event: dict[str, Any]) -> dict[str, Any]:
    clean = dict(event)
    clean.setdefault("observed_at", _now())
    return clean


def _coerce(value: Any) -> Any:
    number = _as_float(value)
    return number if number is not None else value


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected mapping")
    return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
