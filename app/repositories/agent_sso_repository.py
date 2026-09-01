"""One-time tickets used to hand the Feishu identity to xmshouxi."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
import secrets

import psycopg


class AgentSSOTicketRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = (dsn or os.getenv("DATABASE_URL") or "").strip()

    def ensure_schema(self) -> None:
        if not self.dsn:
            return
        with psycopg.connect(self.dsn, connect_timeout=5, autocommit=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_sso_tickets (
                    token_hash TEXT PRIMARY KEY,
                    open_id TEXT NOT NULL,
                    target_path TEXT NOT NULL DEFAULT '/',
                    expires_at TIMESTAMPTZ NOT NULL,
                    used_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_agent_sso_tickets_expires_at
                ON agent_sso_tickets (expires_at)
                """
            )

    def issue(self, open_id: str, target_path: str, ttl_seconds: int = 60) -> str:
        if not self.dsn:
            raise RuntimeError("工作流数据库未配置，无法创建 Agent 登录票据")
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            connection.execute(
                """
                INSERT INTO agent_sso_tickets (token_hash, open_id, target_path, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (self._hash(token), open_id, target_path, expires_at),
            )
            connection.commit()
        return token

    def consume(self, token: str) -> dict[str, str] | None:
        if not self.dsn or not token:
            return None
        now = datetime.now(timezone.utc)
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            row = connection.execute(
                """
                SELECT open_id, target_path, expires_at, used_at
                FROM agent_sso_tickets
                WHERE token_hash = %s
                FOR UPDATE
                """,
                (self._hash(token),),
            ).fetchone()
            if not row or row[3] is not None or row[2] <= now:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE agent_sso_tickets SET used_at = %s WHERE token_hash = %s",
                (now, self._hash(token)),
            )
            connection.commit()
        return {"open_id": str(row[0]), "target_path": str(row[1])}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
