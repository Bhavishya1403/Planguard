"""The stable, ordered catalog of PlanGuard analyzer rules."""

from __future__ import annotations

from dataclasses import dataclass

from planguard.analyzer.rules import (
    AnalysisContext,
    RuleEvaluator,
    pg001,
    pg002,
    pg003,
    pg004,
    pg005,
    pg006,
    pg007,
    pg008,
    pg009,
    pg010,
    pg011,
    pg012,
    pg013,
    pg014,
)
from planguard.models import Finding


@dataclass(frozen=True)
class Rule:
    """One registered migration analysis rule."""

    rule_id: str
    title: str
    evaluate: RuleEvaluator


RULES: tuple[Rule, ...] = (
    Rule("PG001", "Non-concurrent index creation", pg001),
    Rule("PG002", "Foreign key validation", pg002),
    Rule("PG003", "Check constraint validation", pg003),
    Rule("PG004", "SET NOT NULL validation scan", pg004),
    Rule("PG005", "Volatile column default", pg005),
    Rule("PG006", "Table-rewriting type change", pg006),
    Rule("PG007", "Application compatibility risk", pg007),
    Rule("PG008", "Constraint-backed uniqueness", pg008),
    Rule("PG009", "Non-concurrent index drop", pg009),
    Rule("PG010", "Maintenance operations", pg010),
    Rule("PG011", "Concurrent index in a transaction", pg011),
    Rule("PG012", "Accumulated DDL locks", pg012),
    Rule("PG013", "Missing lock timeout", pg013),
    Rule("PG014", "Missing referencing-table index", pg014),
)


def get_rule(rule_id: str) -> Rule:
    """Return a registered rule by its stable identifier."""
    for rule in RULES:
        if rule.rule_id == rule_id:
            return rule
    raise KeyError(f"unknown PlanGuard rule {rule_id!r}")


def evaluate_rule(rule_id: str, context: AnalysisContext) -> list[Finding]:
    """Evaluate one registered rule for a parsed migration."""
    return get_rule(rule_id).evaluate(context)
