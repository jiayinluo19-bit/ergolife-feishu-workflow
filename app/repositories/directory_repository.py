"""Employee directory and lifecycle-role membership storage."""

from __future__ import annotations

import logging
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
    is_tenant_manager: bool = False

    @property
    def department(self) -> str | None:
        return self.department_names[0] if self.department_names else None


class DirectoryRepository:
    """Persist directory users and derive many-to-many role membership."""

    def __init__(
        self,
        dsn: str | None = None,
        role_rules: dict[str, str] | None = None,
        known_roles: Iterable[str] = (),
        admin_open_ids: Iterable[str] = (),
    ) -> None:
        self.dsn = (dsn or "").strip()
        self.role_rules = {str(key): str(value) for key, value in (role_rules or {}).items()}
        self.known_roles = {str(role) for role in known_roles if str(role)} | set(self.role_rules.values())
        self.admin_open_ids = {str(item).strip() for item in admin_open_ids if str(item).strip()}
        self._users: dict[str, DirectoryUser] = {}
        self._members: dict[str, set[str]] = {}
        self._manual_roles: dict[str, set[str]] = {}
        self._role_overrides: dict[str, dict[str, bool]] = {}
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
                    is_tenant_manager BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                ALTER TABLE directory_users ADD COLUMN IF NOT EXISTS is_tenant_manager BOOLEAN NOT NULL DEFAULT FALSE;
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
                CREATE TABLE IF NOT EXISTS directory_role_overrides (
                    open_id TEXT NOT NULL REFERENCES directory_users(open_id) ON DELETE CASCADE,
                    role_code TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (open_id, role_code)
                );
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
        is_tenant_manager: bool = False,
    ) -> DirectoryUser:
        key = str(open_id or "").strip()
        if not key:
            raise ValueError("员工目录缺少 open_id")
        user = DirectoryUser(
            open_id=key,
            user_id=str(user_id).strip() if user_id else None,
            display_name=str(display_name or "未命名用户").strip(),
            email=str(email).strip() if email else None,
            job_title=str(job_title).strip() if job_title else None,
            department_ids=tuple(dict.fromkeys(str(item) for item in department_ids if item)),
            department_names=tuple(dict.fromkeys(str(item) for item in department_names if item)),
            active=bool(active),
            is_tenant_manager=bool(is_tenant_manager),
        )
        if not self.dsn:
            self._users[key] = user
            self._refresh_memory_roles(user)
            return user
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO directory_users
                    (open_id, user_id, display_name, email, job_title,
                     department_ids, department_names, active, is_tenant_manager)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (open_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    display_name = EXCLUDED.display_name,
                    email = EXCLUDED.email,
                    job_title = EXCLUDED.job_title,
                    department_ids = EXCLUDED.department_ids,
                    department_names = EXCLUDED.department_names,
                    active = EXCLUDED.active,
                    is_tenant_manager = EXCLUDED.is_tenant_manager,
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
                    user.is_tenant_manager,
                ),
            )
            connection.execute("DELETE FROM directory_role_members WHERE open_id = %s AND source = 'auto'", (key,))
            blocked = self._db_blocked_roles(connection, key)
            for role in self._matching_roles(user, self._db_role_rules(connection)):
                if role in blocked:
                    continue
                connection.execute(
                    """
                    INSERT INTO directory_role_members (open_id, role_code, source, active)
                    VALUES (%s, %s, 'auto', TRUE)
                    ON CONFLICT (open_id, role_code) DO UPDATE SET active = TRUE, updated_at = NOW()
                    """,
                    (key, role),
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
                       department_ids, department_names, active, is_tenant_manager
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
                """
                SELECT m.role_code
                FROM directory_role_members m
                LEFT JOIN directory_role_overrides o
                  ON o.open_id = m.open_id AND o.role_code = m.role_code
                WHERE m.open_id = %s AND m.active = TRUE
                  AND (o.enabled IS NULL OR o.enabled = TRUE)
                ORDER BY m.role_code
                """,
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
                       u.department_ids, u.department_names, u.active, u.is_tenant_manager
                FROM directory_users u
                JOIN directory_role_members m ON m.open_id = u.open_id
                LEFT JOIN directory_role_overrides o ON o.open_id = m.open_id AND o.role_code = m.role_code
                WHERE m.role_code = %s AND m.active = TRUE AND u.active = TRUE
                  AND (o.enabled IS NULL OR o.enabled = TRUE)
                ORDER BY u.display_name, u.open_id
                """,
                (role,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def is_admin(self, open_id: str | None) -> bool:
        key = str(open_id or "").strip()
        user = self.get_user(key)
        return bool(key and (key in self.admin_open_ids or (user and user.is_tenant_manager)))

    def list_users(self) -> list[dict[str, Any]]:
        if not self.dsn:
            users = sorted(self._users.values(), key=lambda item: (item.display_name, item.open_id))
        else:
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                rows = connection.execute(
                    """
                    SELECT open_id, user_id, display_name, email, job_title,
                           department_names, active, is_tenant_manager
                    FROM directory_users ORDER BY display_name, open_id
                    """
                ).fetchall()
            users = [
                self._from_row((row[0], row[1], row[2], row[3], row[4], [], row[5], row[6], row[7]))
                for row in rows
            ]
        return [
            {
                "open_id": user.open_id,
                "user_id": user.user_id,
                "display_name": user.display_name,
                "email": user.email,
                "job_title": user.job_title,
                "department_names": list(user.department_names),
                "active": user.active,
                "is_tenant_manager": user.is_tenant_manager,
                "roles": self.roles_for_user(user.open_id),
            }
            for user in users
        ]

    def list_role_rules(self) -> dict[str, str]:
        if not self.dsn:
            return dict(self.role_rules)
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            return self._db_role_rules(connection)

    def set_role_rule(self, department_key: str, role_code: str) -> None:
        key, role = str(department_key or "").strip(), str(role_code or "").strip()
        if not key or not role:
            raise ValueError("部门和角色不能为空")
        self.role_rules[key] = role
        self.known_roles.add(role)
        if not self.dsn:
            for user in self._users.values():
                self._refresh_memory_roles(user)
            return
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO lifecycle_role_rules (department_key, role_code)
                VALUES (%s, %s)
                ON CONFLICT (department_key) DO UPDATE SET role_code = EXCLUDED.role_code, updated_at = NOW()
                """,
                (key, role),
            )
            self._refresh_db_auto_roles(connection)

    def remove_role_rule(self, department_key: str) -> None:
        key = str(department_key or "").strip()
        if not key:
            return
        self.role_rules.pop(key, None)
        if not self.dsn:
            for user in self._users.values():
                self._refresh_memory_roles(user)
            return
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("DELETE FROM lifecycle_role_rules WHERE department_key = %s", (key,))
            self._refresh_db_auto_roles(connection)

    def set_manual_roles(self, open_id: str, roles: Iterable[str]) -> None:
        key = str(open_id or "").strip()
        selected = {str(role).strip() for role in roles if str(role).strip()}
        unknown = selected - self.known_roles
        if unknown:
            raise ValueError(f"未知角色: {', '.join(sorted(unknown))}")
        if not self.dsn:
            if key not in self._users:
                raise KeyError(key)
            self._manual_roles[key] = selected
            self._role_overrides[key] = {role: role in selected for role in self.known_roles}
            self._refresh_memory_roles(self._users[key])
            return
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            if not connection.execute("SELECT 1 FROM directory_users WHERE open_id = %s", (key,)).fetchone():
                raise KeyError(key)
            connection.execute("DELETE FROM directory_role_members WHERE open_id = %s AND source = 'manual'", (key,))
            connection.execute("DELETE FROM directory_role_overrides WHERE open_id = %s", (key,))
            for role in self.known_roles:
                connection.execute(
                    "INSERT INTO directory_role_overrides (open_id, role_code, enabled) VALUES (%s, %s, %s)",
                    (key, role, role in selected),
                )
            for role in selected:
                connection.execute(
                    """
                    INSERT INTO directory_role_members (open_id, role_code, source, active)
                    VALUES (%s, %s, 'manual', TRUE)
                    ON CONFLICT (open_id, role_code) DO UPDATE SET source = 'manual', active = TRUE, updated_at = NOW()
                    """,
                    (key, role),
                )

    def clear_manual_roles(self, open_id: str) -> None:
        key = str(open_id or "").strip()
        if not self.dsn:
            self._manual_roles.pop(key, None)
            self._role_overrides.pop(key, None)
            user = self._users.get(key)
            if user:
                self._refresh_memory_roles(user)
            return
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("DELETE FROM directory_role_members WHERE open_id = %s AND source = 'manual'", (key,))
            connection.execute("DELETE FROM directory_role_overrides WHERE open_id = %s", (key,))
            self._refresh_db_auto_roles(connection, only_open_id=key)

    def _refresh_memory_roles(self, user: DirectoryUser) -> None:
        for members in self._members.values():
            members.discard(user.open_id)
        blocked = {role for role, enabled in self._role_overrides.get(user.open_id, {}).items() if not enabled}
        roles = set(self._matching_roles(user)) - blocked
        roles.update(self._manual_roles.get(user.open_id, set()))
        if not user.active:
            roles = set()
        for role in roles:
            self._members.setdefault(role, set()).add(user.open_id)

    def _refresh_db_auto_roles(self, connection: psycopg.Connection, only_open_id: str | None = None) -> None:
        query = """
            SELECT open_id, user_id, display_name, email, job_title,
                   department_ids, department_names, active, is_tenant_manager
            FROM directory_users
        """
        params: tuple[Any, ...] = ()
        if only_open_id:
            query += " WHERE open_id = %s"
            params = (only_open_id,)
        rows = connection.execute(query, params).fetchall()
        rules = self._db_role_rules(connection)
        for row in rows:
            user = self._from_row(row)
            connection.execute("DELETE FROM directory_role_members WHERE open_id = %s AND source = 'auto'", (user.open_id,))
            if not user.active:
                continue
            blocked = self._db_blocked_roles(connection, user.open_id)
            for role in self._matching_roles(user, rules):
                if role in blocked:
                    continue
                connection.execute(
                    "INSERT INTO directory_role_members (open_id, role_code, source, active) VALUES (%s, %s, 'auto', TRUE) ON CONFLICT (open_id, role_code) DO UPDATE SET active = TRUE, updated_at = NOW()",
                    (user.open_id, role),
                )

    @staticmethod
    def _db_blocked_roles(connection: psycopg.Connection, open_id: str) -> set[str]:
        rows = connection.execute(
            "SELECT role_code FROM directory_role_overrides WHERE open_id = %s AND enabled = FALSE",
            (open_id,),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _matching_roles(self, user: DirectoryUser, rules: dict[str, str] | None = None) -> list[str]:
        effective_rules = rules or self.role_rules
        keys = set(user.department_ids) | set(user.department_names)
        return sorted({role for key, role in effective_rules.items() if key in keys})

    @staticmethod
    def _db_role_rules(connection: psycopg.Connection) -> dict[str, str]:
        rows = connection.execute("SELECT department_key, role_code FROM lifecycle_role_rules").fetchall()
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
            is_tenant_manager=bool(row[8]),
        )


def role_rules_from_assignments(assignments: dict[str, Any]) -> dict[str, str]:
    return {
        str(assignment.department): str(role)
        for role, assignment in assignments.items()
        if getattr(assignment, "department", None)
    }
