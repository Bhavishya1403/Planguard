"""AST-based PostgreSQL migration rules and analysis entry point."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast

from pglast import ast
from pglast.enums import (
    AlterTableType,
    ConstrType,
    NullTestType,
    ObjectType,
    TransactionStmtKind,
    VariableSetKind,
)

from planguard.analyzer.locks import LockInfo, PostgresVersion, lock_info_for
from planguard.analyzer.parser import ParsedStatement, parse_migration
from planguard.models import Finding, Severity

_VOLATILE_BUILTINS = {
    "clock_timestamp",
    "currval",
    "lastval",
    "nextval",
    "random",
    "setval",
    "timeofday",
}
_KNOWN_NONVOLATILE_BUILTINS = {
    "current_date",
    "current_time",
    "current_timestamp",
    "localtime",
    "localtimestamp",
    "now",
    "statement_timestamp",
    "transaction_timestamp",
}
_LOCK_TIMEOUT_MODES = {
    "ACCESS EXCLUSIVE",
    "SHARE",
    "SHARE ROW EXCLUSIVE",
    "EXCLUSIVE",
}
_BINARY_COERCIBLE_TYPES = {("text", "varchar"), ("varchar", "text")}
RelationKey = tuple[str | None, str]


@dataclass(frozen=True)
class AnalysisContext:
    """Parsed migration plus the PostgreSQL target version for every rule."""

    source: str
    statements: tuple[ParsedStatement, ...]
    pg_version: PostgresVersion


RuleEvaluator = Callable[[AnalysisContext], list[Finding]]


def _name(value: object) -> str | None:
    if isinstance(value, ast.String):
        return cast(str, value.sval)
    if isinstance(value, str):
        return value
    return None


def _relation_key(value: object) -> RelationKey | None:
    schema = getattr(value, "schemaname", None)
    relation = getattr(value, "relname", None)
    if not isinstance(relation, str):
        return None
    return (schema if isinstance(schema, str) else None, relation)


def _table_key(statement: ParsedStatement) -> RelationKey | None:
    return _relation_key(getattr(statement.node, "relation", None))


def _node_children(node: ast.Node) -> Iterator[object]:
    for field in node:
        yield getattr(node, field)


def _has_option(options: object, option_name: str) -> bool:
    return any(getattr(option, "defname", None) == option_name for option in _as_sequence(options))


def _walk(value: object) -> Iterator[ast.Node]:
    if isinstance(value, ast.Node):
        yield value
        for child in _node_children(value):
            yield from _walk(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)


def _as_sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, (tuple, list)):
        return tuple(value)
    return ()


def _finding(
    rule_id: str,
    severity: Severity,
    statement: ParsedStatement,
    explanation: str,
    *,
    lock: LockInfo | None = None,
    rewrite: str | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        statement=statement.sql,
        line=statement.line,
        lock_mode=lock.mode if lock is not None else "N/A",
        blocks=lock.blocks if lock is not None else "none",
        explanation=explanation,
        rewrite=rewrite,
    )


def _alter_commands(statement: ParsedStatement) -> tuple[ast.AlterTableCmd, ...]:
    if isinstance(statement.node, ast.AlterTableStmt):
        return tuple(cast(ast.AlterTableCmd, item) for item in _as_sequence(statement.node.cmds))
    return ()


def _add_constraint(
    statement: ParsedStatement,
) -> Iterator[tuple[ast.AlterTableCmd, ast.Constraint]]:
    for command in _alter_commands(statement):
        if getattr(command, "subtype", None) == AlterTableType.AT_AddConstraint and isinstance(
            getattr(command, "def_", None), ast.Constraint
        ):
            yield command, cast(ast.Constraint, command.def_)


def _create_column_defaults(statement: ParsedStatement) -> Iterator[tuple[object, object]]:
    node = statement.node
    if isinstance(node, ast.CreateStmt):
        for element in _as_sequence(node.tableElts):
            if isinstance(element, ast.ColumnDef):
                for constraint in _as_sequence(element.constraints):
                    if (
                        isinstance(constraint, ast.Constraint)
                        and constraint.contype == ConstrType.CONSTR_DEFAULT
                    ):
                        yield element, constraint.raw_expr

    for command in _alter_commands(statement):
        if getattr(command, "subtype", None) == AlterTableType.AT_AddColumn and isinstance(
            getattr(command, "def_", None), ast.ColumnDef
        ):
            column = cast(ast.ColumnDef, command.def_)
            for constraint in _as_sequence(column.constraints):
                if (
                    isinstance(constraint, ast.Constraint)
                    and constraint.contype == ConstrType.CONSTR_DEFAULT
                ):
                    yield column, constraint.raw_expr


def _is_null_default(expression: object) -> bool:
    return isinstance(expression, ast.A_Const) and bool(expression.isnull)


def _default_volatility(expression: object) -> str:
    """Classify known built-ins; unknown function volatility requires catalog lookup."""
    unknown_call = False
    for node in _walk(expression):
        if not isinstance(node, ast.FuncCall):
            continue
        names = tuple(
            name.casefold()
            for part in _as_sequence(node.funcname)
            if (name := _name(part)) is not None
        )
        function_name = names[-1] if names else ""
        if function_name in _VOLATILE_BUILTINS:
            return "volatile"
        if function_name and function_name not in _KNOWN_NONVOLATILE_BUILTINS:
            unknown_call = True
    return "unknown" if unknown_call else "nonvolatile"


def _columns_from_constraint(constraint: ast.Constraint) -> tuple[str, ...]:
    return tuple(
        name for value in _as_sequence(getattr(constraint, "keys", None)) if (name := _name(value))
    )


def _check_column(expression: object) -> str | None:
    if not isinstance(expression, ast.NullTest):
        return None
    if expression.nulltesttype != NullTestType.IS_NOT_NULL:
        return None
    argument = expression.arg
    if not isinstance(argument, ast.ColumnRef):
        return None
    fields = tuple(
        name for value in _as_sequence(argument.fields) if (name := _name(value)) is not None
    )
    return fields[-1] if fields else None


def _all_checks(
    statement: ParsedStatement,
) -> Iterator[tuple[str | None, object, bool, str | None]]:
    """Yield check name, expression, validated state, and optional column."""
    node = statement.node
    if isinstance(node, ast.CreateStmt):
        for element in _as_sequence(node.tableElts):
            if isinstance(element, ast.Constraint) and element.contype == ConstrType.CONSTR_CHECK:
                column = _check_column(element.raw_expr)
                yield element.conname, element.raw_expr, not element.skip_validation, column
            elif isinstance(element, ast.ColumnDef):
                for constraint in _as_sequence(element.constraints):
                    if (
                        isinstance(constraint, ast.Constraint)
                        and constraint.contype == ConstrType.CONSTR_CHECK
                    ):
                        column = _check_column(constraint.raw_expr)
                        yield constraint.conname, constraint.raw_expr, True, column

    for _command, constraint in _add_constraint(statement):
        if constraint.contype == ConstrType.CONSTR_CHECK:
            column = _check_column(constraint.raw_expr)
            yield (
                constraint.conname,
                constraint.raw_expr,
                not constraint.skip_validation,
                column,
            )


def _target_type(column: object) -> str | None:
    type_name = getattr(column, "typeName", None)
    names = tuple(
        name for value in _as_sequence(getattr(type_name, "names", None)) if (name := _name(value))
    )
    return names[-1].casefold() if names else None


def _type_signature(column: object) -> tuple[str, int] | None:
    name = _target_type(column)
    type_name = getattr(column, "typeName", None)
    typemod = getattr(type_name, "typemod", -1)
    if name is None or not isinstance(typemod, int):
        return None
    return name, typemod


def pg001(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        if isinstance(statement.node, ast.IndexStmt) and not statement.node.concurrent:
            findings.append(
                _finding(
                    "PG001",
                    "WARN",
                    statement,
                    (
                        "A regular CREATE INDEX takes a SHARE lock on the table "
                        "and blocks concurrent "
                        "writes until the build finishes."
                    ),
                    lock=lock_info_for(statement.node, context.pg_version),
                    rewrite=(
                        "CREATE INDEX CONCURRENTLY ... (run outside an explicit transaction block)"
                    ),
                )
            )
    return findings


def pg002(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        for _command, constraint in _add_constraint(statement):
            if constraint.contype == ConstrType.CONSTR_FOREIGN and not constraint.skip_validation:
                findings.append(
                    _finding(
                        "PG002",
                        "WARN",
                        statement,
                        (
                            "Adding a validated foreign key checks existing rows and takes SHARE "
                            "ROW EXCLUSIVE locks on the referencing and referenced "
                            "tables; concurrent "
                            "writes can wait for the scan."
                        ),
                        lock=lock_info_for(statement.node, context.pg_version),
                        rewrite=(
                            "ADD CONSTRAINT ... FOREIGN KEY (...) REFERENCES ... NOT VALID; then "
                            "VALIDATE CONSTRAINT in a separate step."
                        ),
                    )
                )
    return findings


def pg003(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        for _command, constraint in _add_constraint(statement):
            if constraint.contype == ConstrType.CONSTR_CHECK and not constraint.skip_validation:
                findings.append(
                    _finding(
                        "PG003",
                        "WARN",
                        statement,
                        (
                            "Adding a validated CHECK scans existing rows while ALTER TABLE holds "
                            "ACCESS EXCLUSIVE, which blocks ordinary reads and writes until the "
                            "command commits."
                        ),
                        lock=lock_info_for(statement.node, context.pg_version),
                        rewrite=(
                            "ADD CONSTRAINT ... CHECK (...) NOT VALID; then VALIDATE CONSTRAINT "
                            "in a separate step."
                        ),
                    )
                )
    return findings


def pg004(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    validated: set[tuple[RelationKey, str]] = set()
    named_checks: dict[tuple[RelationKey, str], str] = {}

    for statement in context.statements:
        key = _table_key(statement)
        if key is None:
            continue
        for command in _alter_commands(statement):
            subtype = getattr(command, "subtype", None)
            column_name = getattr(command, "name", None)
            if subtype == AlterTableType.AT_ValidateConstraint and isinstance(column_name, str):
                check_column = named_checks.get((key, column_name))
                if check_column is not None:
                    validated.add((key, check_column))
            elif subtype == AlterTableType.AT_SetNotNull and isinstance(column_name, str):
                if not (context.pg_version.at_least(12) and (key, column_name) in validated):
                    findings.append(
                        _finding(
                            "PG004",
                            "WARN",
                            statement,
                            (
                                "SET NOT NULL scans the table unless PostgreSQL 12+ "
                                "can use a previously "
                                "validated CHECK (column IS NOT NULL). No suitable "
                                "earlier validated "
                                "check was found in this migration; existing catalog "
                                "constraints were "
                                "not inspected."
                            ),
                            lock=lock_info_for(statement.node, context.pg_version),
                            rewrite=(
                                "ADD CHECK (column IS NOT NULL) NOT VALID; VALIDATE "
                                "CONSTRAINT; then "
                                "SET NOT NULL."
                            ),
                        )
                    )

        # A check added or validated in the same ALTER TABLE statement is not
        # treated as a pre-existing validation proof for SET NOT NULL.
        for name, _expression, is_valid, column in _all_checks(statement):
            if name is not None and column is not None:
                named_checks[(key, name)] = column
            if is_valid and column is not None:
                validated.add((key, column))
    return findings


def pg005(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        for column, expression in _create_column_defaults(statement):
            if _is_null_default(expression):
                continue
            volatility = _default_volatility(expression)
            old_server_rewrite = not context.pg_version.at_least(11)
            if volatility == "volatile" or old_server_rewrite:
                name = getattr(column, "colname", "column")
                condition = (
                    "A volatile default evaluates for existing rows and forces a table rewrite."
                    if volatility == "volatile"
                    else (
                        "Before PostgreSQL 11, adding a column with a non-NULL default rewrites "
                        "existing rows."
                    )
                )
                findings.append(
                    _finding(
                        "PG005",
                        "CRITICAL",
                        statement,
                        f"Adding column {name!r}: {condition}"
                        + (
                            " PostgreSQL 11+ avoids the rewrite for non-volatile "
                            "defaults, including "
                            "constants; a nullable column without a default is not flagged."
                        ),
                        lock=lock_info_for(statement.node, context.pg_version),
                        rewrite=(
                            "Add the column without a default, backfill in bounded batches, then "
                            "set the desired default."
                        ),
                    )
                )
            elif volatility == "unknown":
                findings.append(
                    _finding(
                        "PG005",
                        "WARN",
                        statement,
                        (
                            "The added column default calls a function whose "
                            "volatility is not known "
                            "from SQL syntax alone. Inspect its pg_proc volatility in the target "
                            "database; if it is VOLATILE, existing rows are rewritten."
                        ),
                        lock=lock_info_for(statement.node, context.pg_version),
                    )
                )
    return findings


def pg006(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    types: dict[tuple[RelationKey, str], tuple[str, int]] = {}
    for statement in context.statements:
        key = _table_key(statement)
        node = statement.node
        if key is not None and isinstance(node, ast.CreateStmt):
            for element in _as_sequence(node.tableElts):
                if isinstance(element, ast.ColumnDef) and (target := _type_signature(element)):
                    types[(key, element.colname)] = target
        if key is None:
            continue
        for command in _alter_commands(statement):
            column_name = getattr(command, "name", None)
            definition = getattr(command, "def_", None)
            target = _type_signature(definition)
            subtype = getattr(command, "subtype", None)
            if (
                subtype == AlterTableType.AT_AddColumn
                and isinstance(definition, ast.ColumnDef)
                and target is not None
            ):
                types[(key, definition.colname)] = target
                continue
            if (
                subtype != AlterTableType.AT_AlterColumnType
                or not isinstance(column_name, str)
                or target is None
            ):
                continue
            old_signature = types.get((key, column_name))
            old_type = old_signature[0] if old_signature is not None else None
            target_type, target_typemod = target
            transform = getattr(command, "transform", None)
            if (
                old_signature is not None
                and (old_type, target_type) in _BINARY_COERCIBLE_TYPES
                and old_signature[1] == -1
                and target_typemod == -1
                and transform is None
            ):
                types[(key, column_name)] = target
                continue
            if old_signature == target and transform is None:
                types[(key, column_name)] = target
                continue
            certainty = (
                (
                    f"The parsed schema shows {old_signature} to {target}, "
                    "a conversion that normally rewrites the table and its indexes."
                )
                if old_type is not None
                else (
                    "The prior column type is not declared in this migration, so binary "
                    "coercibility cannot be established statically. PostgreSQL normally "
                    "rewrites the table; confirm the existing type and conversion in the "
                    "target database."
                )
            )
            findings.append(
                _finding(
                    "PG006",
                    "CRITICAL" if old_type is not None else "WARN",
                    statement,
                    (
                        f"ALTER COLUMN {column_name} TYPE: {certainty} "
                        "PostgreSQL can avoid a table rewrite for binary-coercible "
                        "conversions; a USING expression may also change this conclusion."
                    ),
                    lock=lock_info_for(statement.node, context.pg_version),
                    rewrite=(
                        "Use an expand-and-contract migration with a new column and a bounded "
                        "backfill when a rewrite is required."
                    ),
                )
            )
            types[(key, column_name)] = target
    return findings


def pg007(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        node = statement.node
        if isinstance(node, ast.DropStmt) and node.removeType in {
            ObjectType.OBJECT_TABLE,
            ObjectType.OBJECT_MATVIEW,
        }:
            findings.append(
                _finding(
                    "PG007",
                    "CRITICAL",
                    statement,
                    (
                        "Dropping a relation can break application versions that still read "
                        "or write it. Use an expand-and-contract rollout and remove it after "
                        "consumers have migrated."
                    ),
                    lock=lock_info_for(node, context.pg_version),
                )
            )
        elif isinstance(node, ast.AlterTableStmt):
            for command in _alter_commands(statement):
                if getattr(command, "subtype", None) in {
                    AlterTableType.AT_DropColumn,
                }:
                    action = (
                        "Dropping a column"
                        if command.subtype == AlterTableType.AT_DropColumn
                        else "Renaming a column"
                    )
                    findings.append(
                        _finding(
                            "PG007",
                            "CRITICAL",
                            statement,
                            (
                                f"{action} can break application versions that still "
                                "reference its old name or data. Use an expand-and-contract "
                                "rollout."
                            ),
                            lock=lock_info_for(node, context.pg_version),
                        )
                    )
        elif isinstance(node, ast.RenameStmt) and node.renameType == ObjectType.OBJECT_TABLE:
            findings.append(
                _finding(
                    "PG007",
                    "CRITICAL",
                    statement,
                    (
                        "Renaming a table can break application versions that still use its "
                        "old name. Use an expand-and-contract rollout."
                    ),
                    lock=lock_info_for(node, context.pg_version),
                )
            )
    return findings


def pg008(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        for _command, constraint in _add_constraint(statement):
            if constraint.contype in {
                ConstrType.CONSTR_UNIQUE,
                ConstrType.CONSTR_PRIMARY,
            } and not getattr(constraint, "indexname", None):
                kind = (
                    "PRIMARY KEY" if constraint.contype == ConstrType.CONSTR_PRIMARY else "UNIQUE"
                )
                findings.append(
                    _finding(
                        "PG008",
                        "WARN",
                        statement,
                        (
                            f"Adding a {kind} constraint builds its supporting index "
                            "while ALTER TABLE holds ACCESS EXCLUSIVE, blocking table "
                            "reads and writes. A pre-built concurrent index can shorten "
                            "the constraint-attachment lock."
                        ),
                        lock=lock_info_for(statement.node, context.pg_version),
                        rewrite=(
                            "CREATE UNIQUE INDEX CONCURRENTLY ...; then ALTER TABLE "
                            "... ADD CONSTRAINT "
                            "... USING INDEX ...;"
                        ),
                    )
                )
    return findings


def pg009(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        node = statement.node
        if (
            isinstance(node, ast.DropStmt)
            and node.removeType == ObjectType.OBJECT_INDEX
            and not node.concurrent
        ):
            findings.append(
                _finding(
                    "PG009",
                    "WARN",
                    statement,
                    (
                        "DROP INDEX without CONCURRENTLY takes an ACCESS EXCLUSIVE lock on the "
                        "index and can block transactions that use it. Concurrent drop reduces "
                        "interference but cannot run inside a transaction block."
                    ),
                    lock=lock_info_for(node, context.pg_version),
                    rewrite=(
                        "DROP INDEX CONCURRENTLY index_name; (run outside an explicit transaction "
                        "block)"
                    ),
                )
            )
    return findings


def pg010(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        node = statement.node
        reason: str | None = None
        rewrite: str | None = None
        if isinstance(node, ast.ReindexStmt):
            concurrent = any(
                getattr(parameter, "defname", None) == "concurrently"
                for parameter in _as_sequence(node.params)
            )
            if not concurrent and context.pg_version.at_least(12):
                reason = (
                    "REINDEX without CONCURRENTLY takes a strong lock and can block writes "
                    "while rebuilding the index."
                )
                rewrite = (
                    "REINDEX INDEX CONCURRENTLY index_name; (supported on PostgreSQL 12+ "
                    "and outside a transaction block)"
                )
        elif isinstance(node, ast.ClusterStmt):
            reason = (
                "CLUSTER rewrites the table under ACCESS EXCLUSIVE, blocking ordinary "
                "reads and writes."
            )
            rewrite = (
                "Schedule CLUSTER in a maintenance window; PostgreSQL does not provide "
                "CLUSTER CONCURRENTLY."
            )
        elif isinstance(node, ast.VacuumStmt) and _has_option(node.options, "full"):
            reason = (
                "VACUUM FULL rewrites the table under ACCESS EXCLUSIVE, blocking ordinary "
                "reads and writes."
            )
            rewrite = (
                "Use regular VACUUM when its space-reclamation behavior is sufficient; "
                "VACUUM FULL has no CONCURRENTLY option."
            )
        elif (
            isinstance(node, ast.RefreshMatViewStmt)
            and not node.concurrent
            and context.pg_version.at_least(9, 4)
        ):
            reason = (
                "REFRESH MATERIALIZED VIEW without CONCURRENTLY takes ACCESS EXCLUSIVE "
                "on the view and blocks reads while refreshing."
            )
            rewrite = (
                "REFRESH MATERIALIZED VIEW CONCURRENTLY view_name; (requires a qualifying "
                "unique index and a suitable server version)"
            )

        if reason is not None:
            findings.append(
                _finding(
                    "PG010",
                    "CRITICAL",
                    statement,
                    reason,
                    lock=lock_info_for(node, context.pg_version),
                    rewrite=rewrite,
                )
            )
    return findings


def pg011(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for statement in context.statements:
        if (
            isinstance(statement.node, ast.IndexStmt)
            and statement.node.concurrent
            and statement.in_transaction
        ):
            findings.append(
                _finding(
                    "PG011",
                    "CRITICAL",
                    statement,
                    (
                        "PostgreSQL rejects CREATE INDEX CONCURRENTLY inside "
                        "an explicit transaction "
                        "block. This statement is in a parsed BEGIN/COMMIT block; a migration "
                        "runner may also wrap scripts in a transaction and "
                        "must be checked separately."
                    ),
                    lock=lock_info_for(statement.node, context.pg_version),
                    rewrite=(
                        "Run CREATE INDEX CONCURRENTLY in a migration step that is not wrapped "
                        "in a transaction."
                    ),
                )
            )
    return findings


def pg012(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    groups: list[list[ParsedStatement]] = []
    current: list[ParsedStatement] = []
    for statement in context.statements:
        node = statement.node
        if isinstance(node, ast.TransactionStmt) and node.kind in {
            TransactionStmtKind.TRANS_STMT_BEGIN,
            TransactionStmtKind.TRANS_STMT_START,
        }:
            if current:
                groups.append(current)
            current = []
            continue
        if isinstance(node, ast.TransactionStmt) and node.kind in {
            TransactionStmtKind.TRANS_STMT_COMMIT,
            TransactionStmtKind.TRANS_STMT_ROLLBACK,
        }:
            if current:
                groups.append(current)
            current = []
            continue
        if statement.in_transaction:
            lock = lock_info_for(statement.node, context.pg_version)
            is_create_table = isinstance(statement.node, ast.CreateStmt)
            if (lock is not None or is_create_table) and not (
                isinstance(statement.node, ast.IndexStmt) and statement.node.concurrent
            ):
                current.append(statement)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    for group in groups:
        if len(group) < 2:
            continue
        first = group[0]
        for statement in group[1:]:
            findings.append(
                _finding(
                    "PG012",
                    "WARN",
                    statement,
                    (
                        "This transaction contains multiple lock-taking statements; "
                        f"locks from the earlier statement on line {first.line} remain "
                        "held until COMMIT or ROLLBACK. Split independent schema changes "
                        "into separate transactions to shorten lock duration."
                    ),
                    lock=lock_info_for(statement.node, context.pg_version),
                )
            )
    return findings


def _timeout_is_positive(statement: ParsedStatement) -> bool | None:
    node = statement.node
    if not isinstance(node, ast.VariableSetStmt) or node.name.casefold() != "lock_timeout":
        return None
    if node.kind != VariableSetKind.VAR_SET_VALUE:
        return False
    args = _as_sequence(node.args)
    if not args:
        return None
    value = args[0]
    if not isinstance(value, ast.A_Const):
        return None
    raw = getattr(value, "val", None)
    if isinstance(raw, ast.String):
        first_piece = raw.sval.strip().split(maxsplit=1)[0] if raw.sval.strip() else "0"
        numeric = first_piece.removesuffix("ms").removesuffix("s").removesuffix("min")
        try:
            return Decimal(numeric) > 0
        except InvalidOperation:
            return None
    if isinstance(raw, ast.Integer):
        return cast(int, raw.ival) > 0
    return None


def _timeout_state_update(
    statement: ParsedStatement, session: bool, local: bool | None
) -> tuple[bool, bool | None]:
    node = statement.node
    if isinstance(node, ast.VariableSetStmt):
        if node.name.casefold() == "lock_timeout":
            value = _timeout_is_positive(statement)
            if node.kind == VariableSetKind.VAR_RESET:
                value = False
            if node.is_local:
                if statement.in_transaction:
                    local = value is True
            else:
                session = value is True
        elif node.kind == VariableSetKind.VAR_RESET_ALL:
            session = False
            if statement.in_transaction:
                local = False
    return session, local


def pg013(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    session_timeout = False
    local_timeout: bool | None = None
    session_at_begin = False
    active = False

    for statement in context.statements:
        node = statement.node
        if isinstance(node, ast.TransactionStmt):
            if node.kind in {
                TransactionStmtKind.TRANS_STMT_BEGIN,
                TransactionStmtKind.TRANS_STMT_START,
            }:
                active = True
                session_at_begin = session_timeout
                local_timeout = None
            elif node.kind in {
                TransactionStmtKind.TRANS_STMT_COMMIT,
                TransactionStmtKind.TRANS_STMT_ROLLBACK,
            }:
                if node.kind == TransactionStmtKind.TRANS_STMT_ROLLBACK:
                    session_timeout = session_at_begin
                local_timeout = None
                active = bool(node.chain)
                if active:
                    session_at_begin = session_timeout
            continue

        lock = lock_info_for(node, context.pg_version)
        effective_timeout = (
            local_timeout if active and local_timeout is not None else session_timeout
        )
        if lock is not None and lock.mode in _LOCK_TIMEOUT_MODES and not effective_timeout:
            findings.append(
                _finding(
                    "PG013",
                    "WARN",
                    statement,
                    (
                        f"This {lock.mode} operation has no preceding effective positive "
                        "lock_timeout in the parsed session/transaction context. "
                        "lock_timeout bounds lock acquisition waits; it does not limit "
                        "the DDL execution time."
                    ),
                    lock=lock,
                    rewrite=(
                        "SET lock_timeout = '5s'; before the DDL (or SET LOCAL inside "
                        "its transaction)."
                    ),
                )
            )
        session_timeout, local_timeout = _timeout_state_update(
            statement, session_timeout, local_timeout
        )
    return findings


@dataclass(frozen=True)
class ForeignKey:
    """A foreign key's referencing relation and ordered columns."""

    statement: ParsedStatement
    relation: RelationKey
    columns: tuple[str, ...]


def _foreign_key_columns(constraint: ast.Constraint, column: str | None = None) -> tuple[str, ...]:
    attrs = _as_sequence(getattr(constraint, "fk_attrs", None))
    if attrs:
        return tuple(name for item in attrs if (name := _name(item)) is not None)
    return (column,) if column is not None else ()


def _defined_indexes(context: AnalysisContext) -> dict[RelationKey, list[tuple[str, ...]]]:
    indexes: dict[RelationKey, list[tuple[str, ...]]] = {}
    for statement in context.statements:
        node = statement.node
        if isinstance(node, ast.IndexStmt):
            key = _relation_key(node.relation)
            if key is None or getattr(node, "whereClause", None) is not None:
                continue
            access_method = getattr(node, "accessMethod", None)
            if access_method not in (None, "btree"):
                continue
            columns: list[str] = []
            for element in _as_sequence(node.indexParams):
                name = getattr(element, "name", None)
                if not isinstance(name, str):
                    columns = []
                    break
                columns.append(name)
            if columns:
                indexes.setdefault(key, []).append(tuple(columns))

        elif isinstance(node, ast.CreateStmt):
            key = _relation_key(node.relation)
            if key is None:
                continue
            for element in _as_sequence(node.tableElts):
                if isinstance(element, ast.Constraint) and element.contype in {
                    ConstrType.CONSTR_PRIMARY,
                    ConstrType.CONSTR_UNIQUE,
                }:
                    constraint_columns = _columns_from_constraint(element)
                    if constraint_columns:
                        indexes.setdefault(key, []).append(tuple(constraint_columns))
                elif isinstance(element, ast.ColumnDef):
                    for constraint in _as_sequence(element.constraints):
                        if isinstance(constraint, ast.Constraint) and constraint.contype in {
                            ConstrType.CONSTR_PRIMARY,
                            ConstrType.CONSTR_UNIQUE,
                        }:
                            constraint_columns = _columns_from_constraint(constraint)
                            if not constraint_columns:
                                constraint_columns = (element.colname,)
                            indexes.setdefault(key, []).append(tuple(constraint_columns))

    return indexes


def _foreign_keys(context: AnalysisContext) -> list[ForeignKey]:
    foreign_keys: list[ForeignKey] = []
    for statement in context.statements:
        key = _table_key(statement)
        if key is None:
            continue
        node = statement.node
        if isinstance(node, ast.CreateStmt):
            for element in _as_sequence(node.tableElts):
                if (
                    isinstance(element, ast.Constraint)
                    and element.contype == ConstrType.CONSTR_FOREIGN
                ):
                    columns = _foreign_key_columns(element)
                    if columns:
                        foreign_keys.append(ForeignKey(statement, key, columns))
                elif isinstance(element, ast.ColumnDef):
                    for constraint in _as_sequence(element.constraints):
                        if (
                            isinstance(constraint, ast.Constraint)
                            and constraint.contype == ConstrType.CONSTR_FOREIGN
                        ):
                            columns = _foreign_key_columns(constraint, element.colname)
                            if columns:
                                foreign_keys.append(ForeignKey(statement, key, columns))
        for _command, constraint in _add_constraint(statement):
            if constraint.contype == ConstrType.CONSTR_FOREIGN:
                columns = _foreign_key_columns(constraint)
                if columns:
                    foreign_keys.append(ForeignKey(statement, key, columns))
    return foreign_keys


def _has_supporting_index(columns: tuple[str, ...], indexes: list[tuple[str, ...]]) -> bool:
    wanted = set(columns)
    return any(
        len(index) >= len(columns) and set(index[: len(columns)]) == wanted for index in indexes
    )


def pg014(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    indexes = _defined_indexes(context)
    seen: set[tuple[RelationKey, tuple[str, ...]]] = set()
    for foreign_key in _foreign_keys(context):
        identity = (foreign_key.relation, foreign_key.columns)
        if identity in seen:
            continue
        seen.add(identity)
        if _has_supporting_index(foreign_key.columns, indexes.get(foreign_key.relation, [])):
            continue
        relation = ".".join(part for part in foreign_key.relation if part is not None)
        column_list = ", ".join(foreign_key.columns)
        findings.append(
            _finding(
                "PG014",
                "WARN",
                foreign_key.statement,
                (
                    "No supporting B-tree index with foreign-key columns "
                    f"({column_list}) as its leading key columns was found in this "
                    f"migration for referencing table {relation}. PostgreSQL does not "
                    "create that index automatically. Existing indexes were not read "
                    "from pg_catalog, so verify catalog state before treating this as "
                    "a confirmed missing index."
                ),
                lock=lock_info_for(foreign_key.statement.node, context.pg_version),
                rewrite=f"CREATE INDEX CONCURRENTLY ON {relation} ({column_list});",
            )
        )
    return findings


def analyze_migration(
    source: str, *, pg_version: PostgresVersion | int | str = 16
) -> list[Finding]:
    """Parse SQL and execute all registered Phase 2 rules without a database."""
    from planguard.analyzer.registry import RULES

    context = AnalysisContext(
        source=source,
        statements=parse_migration(source),
        pg_version=PostgresVersion.parse(pg_version),
    )
    return [finding for rule in RULES for finding in rule.evaluate(context)]
