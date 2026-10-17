"""PostgreSQL AST parsing and source-position tracking for migration files."""

from __future__ import annotations

from dataclasses import dataclass

from pglast import ast, parse_sql
from pglast.enums import TransactionStmtKind
from pglast.parser import scan


@dataclass(frozen=True)
class ParsedStatement:
    """One parsed PostgreSQL statement and its location in the source file."""

    node: ast.Node
    sql: str
    line: int
    start: int
    in_transaction: bool


def _statement_token_start(
    location: int, end: int, token_ranges: list[tuple[int, int, str]]
) -> int:
    """Find a statement's first SQL token, excluding whitespace and comments."""
    for start, _token_end, name in token_ranges:
        if location <= start < end and name not in {"C_COMMENT", "SQL_COMMENT", "ASCII_59"}:
            return start
        if start >= end:
            break
    return location


def _transaction_action(node: ast.Node) -> tuple[bool, bool] | None:
    """Return whether a transaction begins and chains after this transaction command."""
    if not isinstance(node, ast.TransactionStmt):
        return None
    kind = node.kind
    if kind in {
        TransactionStmtKind.TRANS_STMT_BEGIN,
        TransactionStmtKind.TRANS_STMT_START,
    }:
        return True, False
    if kind in {
        TransactionStmtKind.TRANS_STMT_COMMIT,
        TransactionStmtKind.TRANS_STMT_ROLLBACK,
    }:
        return False, bool(node.chain)
    return None


def parse_migration(source: str) -> tuple[ParsedStatement, ...]:
    """Parse a migration with PostgreSQL's grammar and retain statement boundaries.

    Transaction context reflects explicit ``BEGIN``/``START TRANSACTION`` blocks in
    the file. A caller that wraps the whole migration in a transaction must account
    for that runner-specific behavior separately.
    """
    raw_statements = parse_sql(source)
    if not raw_statements:
        return ()

    tokens = [(token.start, token.end + 1, token.name) for token in scan(source)]
    parsed: list[ParsedStatement] = []
    in_transaction = False

    for raw in raw_statements:
        location = raw.stmt_location
        end = location + raw.stmt_len if raw.stmt_len else len(source)
        start = _statement_token_start(location, end, tokens)
        node = raw.stmt
        action = _transaction_action(node)
        if action is not None and action[0]:
            in_transaction = True
        statement_source = source[start:end].strip()
        parsed.append(
            ParsedStatement(
                node=node,
                sql=statement_source,
                line=source.count("\n", 0, start) + 1,
                start=start,
                in_transaction=in_transaction,
            )
        )
        if action is not None and not action[0]:
            in_transaction = action[1]

    return tuple(parsed)
