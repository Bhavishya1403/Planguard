"""Version-aware lock behavior for PostgreSQL migration statements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pglast import ast
from pglast.enums import AlterTableType, ConstrType, ObjectType

Blocks = Literal["reads", "writes", "both", "none"]


@dataclass(frozen=True, order=True)
class PostgresVersion:
    """A PostgreSQL major/minor version used for migration behavior decisions."""

    major: int
    minor: int = 0

    @classmethod
    def parse(cls, value: PostgresVersion | int | str) -> PostgresVersion:
        """Parse modern major-only and legacy major.minor PostgreSQL versions."""
        if isinstance(value, cls):
            return value
        pieces = str(value).split(".")
        try:
            numbers = [int(piece) for piece in pieces]
        except ValueError as error:
            raise ValueError(f"invalid PostgreSQL version: {value!r}") from error
        if not numbers or any(number < 0 for number in numbers):
            raise ValueError(f"invalid PostgreSQL version: {value!r}")
        major = numbers[0]
        minor = numbers[1] if major < 10 and len(numbers) > 1 else 0
        if major == 0:
            raise ValueError(f"invalid PostgreSQL version: {value!r}")
        return cls(major=major, minor=minor)

    def at_least(self, major: int, minor: int = 0) -> bool:
        """Whether this target is at least the supplied PostgreSQL version."""
        return (self.major, self.minor) >= (major, minor)


@dataclass(frozen=True)
class LockInfo:
    """Lock mode and broad classes of concurrent table operations it blocks."""

    mode: str
    blocks: Blocks
    note: str


ACCESS_EXCLUSIVE = LockInfo(
    "ACCESS EXCLUSIVE", "both", "conflicts with ordinary reads and writes on the table"
)
SHARE = LockInfo("SHARE", "writes", "blocks concurrent inserts, updates, and deletes")
SHARE_ROW_EXCLUSIVE = LockInfo(
    "SHARE ROW EXCLUSIVE", "writes", "blocks concurrent inserts, updates, and deletes"
)
SHARE_UPDATE_EXCLUSIVE = LockInfo(
    "SHARE UPDATE EXCLUSIVE", "none", "allows ordinary reads and writes"
)
EXCLUSIVE = LockInfo("EXCLUSIVE", "writes", "allows ordinary reads but blocks concurrent writes")


def _has_option(options: object, option_name: str) -> bool:
    if not isinstance(options, (tuple, list)):
        return False
    return any(getattr(option, "defname", None) == option_name for option in options)


def lock_info_for(node: ast.Node, version: PostgresVersion) -> LockInfo | None:
    """Return the strongest relevant table lock for a supported statement.

    PostgreSQL generally takes ``ACCESS EXCLUSIVE`` for ``ALTER TABLE`` unless a
    subform documents a weaker mode. The matrix intentionally describes table
    effects, not every short-lived catalog lock. See the PostgreSQL lock and DDL
    references in ``docs/analyzer-rules.md``.
    """
    if isinstance(node, ast.IndexStmt):
        return SHARE_UPDATE_EXCLUSIVE if node.concurrent else SHARE

    if isinstance(node, ast.DropStmt):
        if node.removeType == ObjectType.OBJECT_INDEX:
            return SHARE_UPDATE_EXCLUSIVE if node.concurrent else ACCESS_EXCLUSIVE
        if node.removeType in {
            ObjectType.OBJECT_TABLE,
            ObjectType.OBJECT_MATVIEW,
        }:
            return ACCESS_EXCLUSIVE

    if isinstance(node, ast.AlterTableStmt):
        modes: list[LockInfo] = []
        for command in node.cmds:
            subtype = command.subtype
            if subtype == AlterTableType.AT_ValidateConstraint:
                modes.append(SHARE_UPDATE_EXCLUSIVE)
            elif subtype == AlterTableType.AT_AddConstraint:
                constraint = command.def_
                if isinstance(constraint, ast.Constraint):
                    if constraint.contype == ConstrType.CONSTR_FOREIGN:
                        modes.append(SHARE_ROW_EXCLUSIVE)
                    else:
                        modes.append(ACCESS_EXCLUSIVE)
                else:
                    modes.append(ACCESS_EXCLUSIVE)
            else:
                modes.append(ACCESS_EXCLUSIVE)
        if modes:
            return max(modes, key=lambda mode: _LOCK_STRENGTH[mode.mode])

    if isinstance(node, ast.VacuumStmt):
        return ACCESS_EXCLUSIVE if _has_option(node.options, "full") else SHARE_UPDATE_EXCLUSIVE

    if isinstance(node, ast.ReindexStmt):
        concurrent = _has_option(node.params, "concurrently")
        if concurrent and version.at_least(12):
            return SHARE_UPDATE_EXCLUSIVE
        return ACCESS_EXCLUSIVE

    if isinstance(node, ast.ClusterStmt):
        return ACCESS_EXCLUSIVE

    if isinstance(node, ast.RefreshMatViewStmt):
        if node.concurrent and version.at_least(9, 4):
            return EXCLUSIVE
        return ACCESS_EXCLUSIVE

    if isinstance(node, ast.RenameStmt):
        return ACCESS_EXCLUSIVE

    return None


_LOCK_STRENGTH = {
    "SHARE UPDATE EXCLUSIVE": 1,
    "SHARE": 2,
    "SHARE ROW EXCLUSIVE": 3,
    "EXCLUSIVE": 4,
    "ACCESS EXCLUSIVE": 5,
}
