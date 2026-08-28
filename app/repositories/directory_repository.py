"""Employee directory and lifecycle-role membership storage.

The product master remains in ``product_market_parameters``.  This small
repository stores only the Feishu directory projection needed by the
workbench: who the employee is, which departments they belong to, and the
roles inferred from those departments.  It deliberately supports an in-memory
mode so local tests and the demo do not require PostgreSQL.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import psycopg
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectoryUser:
    open_id: str
    user_id: str | None = None
    display_name: str = "未命名用户"
    email: str | None = None
    job_title: str | None = None
    department_ids: tuple[str, ...] = field(default_factory=tuple)
    department_names: tuple[str, ...] = field(default_factory=tuple)
    active: bool = True

    @property
    def department(self) -> str | None:
        """Compatibility display field for the primary department."""
        return self.department_names[0] if self.department_names else None


class DirectoryRepository:
    """Persist directory users and derive many-to-many role membership."""

    def __init__(
        self,
        dsn: str | None = None,
        role_rules: dict[str, str] | None = None,
    ) -> None:
        self.dsn = (dsn or "").strip()
        self.role_rules = {str(key): str(value) for key, value in (role_rules or {}).items()}
        self._users: dict[str, DirectoryUser] = {}
        self._members: dict[str, set[str]] = {}
        if self.dsn:
            self.ensure_schema()
            self._seed_role_rules()

    @property
    def source(self) -> str:
        return "postgres" if self.dsn else "memory"

    def ensure_schema(self) -> None:
        if not self.dsn:
            return
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS directory_users (
                    open_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    display_name TEXT NOT NULL,
                    email TEXT,
                    job_title TEXT,
                    department_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    department_names JSONB NOT NULL DEFAULT '[]'::jsonb,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS lifecycle_role_rules (
                    department_key TEXT PRIMARY KEY,
                    role_code TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS directory_role_members (
                    open_id TEXT NOT NULL REFERENCES directory_users(open_id) ON DELETE CASCADE,
                    role_code TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'auto',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (open_id, role_code)
                );
                CREATE INDEX IF NOT EXISTS directory_role_members_role_idx
                    ON directory_role_members (role_code, active);
                """
            )

    def _seed_role_rules(self) -> None:
        if not self.dsn or not self.role_rules:
            return
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            for department_key, role_code in self.role_rules.items():
                connection.execute(
                    """
                    INSERT INTO lifecycle_role_rules (department_key, role_code)
                    VALUES (%s, %s)
                    ON CONFLICT (department_key) DO UPDATE SET role_code = EXCLUDED.role_code,
                        updated_at = NOW()
                    """,
                    (department_key, role_code),
                )

    def upsert_user(
        self,
        *,
        open_id: str,
        user_id: str | None = None,
        display_name: str = "未命名用户",
        email: str | None = None,
        job_title: str | None = None,
        department_ids: Iterable[str] = (),
        department_names: Iterable[str] = (),
        active: bool = True,
    ) -> DirectoryUser:
        normalized_open_id = str(open_id or "").strip()
        if not normalized_open_id:
            raise ValueError("员工目录缺少 open_id")
        user = DirectoryUser(
            open_id=normalized_open_id,
            user_id=str(user_id).strip() if user_id else None,
            display_name=str(display_name or "未命名用户").strip(),
            email=str(email).strip() if email else None,
            job_title=str(job_title).strip() if job_title else None,
            department_ids=tuple(dict.fromkeys(str(item) for item in department_ids if item)),
            department_names=tuple(dict.fromkeys(str(item) for item in department_names if item)),
            active=bool(active),
        )
        if not self.dsn:
            self._users[user.open_id] = user
            for members in self._members.values():
                members.discard(user.open_id)
            for role in self._matching_roles(user):
                self._members.setdefault(role, set()).add(user.open_id)
            return user
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO directory_users
                    (open_id, user_id, display_name, email, job_title,
                     department_ids, department_names, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (open_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    display_name = EXCLUDED.display_name,
                    email = EXCLUDED.email,
                    job_title = EXCLUDED.job_title,
                    department_ids = EXCLUDED.department_ids,
                    department_names = EXCLUDED.department_names,
                    active = EXCLUDED.active,
                    updated_at = NOW()
                """,
                (
                    user.open_id,
                    user.user_id,
                    user.display_name,
                    user.email,
                    user.job_title,
                    Jsonb(list(user.department_ids)),
                    Jsonb(list(user.department_names)),
                    user.active,
                ),
            )
            connection.execute(
                "DELETE FROM directory_role_members WHERE open_id = %s AND source = 'auto'",
                (user.open_id,),
            )
            role_rules = self._db_role_rules(connection)
            for role in self._matching_roles(user, role_rules):
                connection.execute(
                    """
                    INSERT INTO directory_role_members (open_id, role_code, source, active)
                    VALUES (%s, %s, 'auto', TRUE)
                    ON CONFLICT (open_id, role_code) DO UPDATE SET active = TRUE, updated_at = NOW()
                    """,
                    (user.open_id, role),
                )
        return user

    def get_user(self, open_id: str | None) -> DirectoryUser | None:
        key = str(open_id or "").strip()
        if not key:
            return None
        if not self.dsn:
            return self._users.get(key)
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            row = connection.execute(
                """
                SELECT open_id, user_id, display_name, email, job_title,
                       department_ids, department_names, active
                FROM directory_users WHERE open_id = %s
                """,
                (key,),
            ).fetchone()
        return self._from_row(row) if row else None

    def roles_for_user(self, open_id: str | None) -> list[str]:
        key = str(open_id or "").strip()
        if not key:
            return []
        if not self.dsn:
            return sorted(role for role, members in self._members.items() if key in members)
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            rows = connection.execute(
                "SELECT role_code FROM directory_role_members WHERE open_id = %s AND active = TRUE ORDER BY role_code",
                (key,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def members_for_role(self, role_code: str) -> list[DirectoryUser]:
        role = str(role_code or "").strip()
        if not role:
            return []
        if not self.dsn:
            return [self._users[key] for key in sorted(self._members.get(role, set())) if key in self._users]
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            rows = connection.execute(
                """
                SELECT u.open_id, u.user_id, u.display_name, u.email, u.job_title,
                       u.department_ids, u.department_names, u.active
                FROM directory_users u
                JOIN directory_role_members m ON m.open_id = u.open_id
                WHERE m.role_code = %s AND m.active = TRUE AND u.active = TRUE
                ORDER BY u.display_name, u.open_id
                """,
                (role,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _matching_roles(self, user: DirectoryUser, rules: dict[str, str] | None = None) -> list[str]:
        effective_rules = rules or self.role_rules
        keys = set(user.department_ids) | set(user.department_names)
        return sorted({role for key, role in effective_rules.items() if key in keys})

    def _db_role_rules(self, connection: psycopg.Connection) -> dict[str, str]:
        rows = connection.execute(
            "SELECT department_key, role_code FROM lifecycle_role_rules"
        ).fetchall()
        return {str(key): str(role) for key, role in rows}

    @staticmethod
    def _from_row(row: tuple[Any, ...]) -> DirectoryUser:
        return DirectoryUser(
            open_id=str(row[0]),
            user_id=str(row[1]) if row[1] else None,
            display_name=str(row[2] or "未命名用户"),
            email=str(row[3]) if row[3] else None,
            job_title=str(row[4]) if row[4] else None,
            department_ids=tuple(str(item) for item in (row[5] or [])),
            department_names=tuple(str(item) for item in (row[6] or [])),
            active=bool(row[7]),
        )


def role_rules_from_assignments(assignments: dict[str, Any]) -> dict[str, str]:
    """Build default department-name rules from the existing role YAML."""
    return {
        str(assignment.department): str(role)
        for role, assignment in assignments.items()
        if getattr(assignment, "department", None)
    }
