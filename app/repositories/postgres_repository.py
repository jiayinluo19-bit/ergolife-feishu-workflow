"""PostgreSQL persistence for the workflow aggregate.

The MVP stores the Pydantic payloads as JSONB so the repository can evolve
without coupling the workflow core to a particular database schema.  The
important query keys (project/node/event ids and timestamps) remain ordinary
columns for filtering and ordering.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb

from ..domain.models import AuditEvent, NodeInstance, ProductProject
from .interfaces import Repository


class PostgresRepository(Repository):
    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DATABASE_URL 不能为空")
        self.dsn = dsn
        self.ensure_schema()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            yield connection

    def ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_projects (
                    id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS workflow_nodes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS workflow_nodes_project_idx
                    ON workflow_nodes (project_id, updated_at);
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    node_instance_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_events_project_idx
                    ON workflow_events (project_id, created_at);
                """
            )

    @property
    def projects(self) -> dict[str, ProductProject]:
        with self._connection() as connection:
            rows = connection.execute("SELECT payload FROM workflow_projects ORDER BY updated_at, id").fetchall()
        return {payload["id"]: ProductProject.model_validate(payload) for (payload,) in rows}

    @property
    def nodes(self) -> dict[str, NodeInstance]:
        with self._connection() as connection:
            rows = connection.execute("SELECT payload FROM workflow_nodes ORDER BY updated_at, id").fetchall()
        return {payload["id"]: NodeInstance.model_validate(payload) for (payload,) in rows}

    @property
    def events(self) -> list[AuditEvent]:
        with self._connection() as connection:
            rows = connection.execute("SELECT payload FROM workflow_events ORDER BY created_at, id").fetchall()
        return [AuditEvent.model_validate(payload) for (payload,) in rows]

    def save_project(self, project: ProductProject) -> ProductProject:
        payload = project.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO workflow_projects (id, payload)
                VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (project.id, Jsonb(payload)),
            )
        return project

    def get_project(self, project_id: str) -> ProductProject:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM workflow_projects WHERE id = %s", (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        return ProductProject.model_validate(row[0])

    def save_node(self, node: NodeInstance) -> NodeInstance:
        payload = node.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO workflow_nodes (id, project_id, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET project_id = EXCLUDED.project_id,
                    payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (node.id, node.project_id, Jsonb(payload)),
            )
        return node

    def delete_node(self, node_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM workflow_nodes WHERE id = %s", (node_id,))

    def get_node(self, node_id: str) -> NodeInstance:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM workflow_nodes WHERE id = %s", (node_id,)).fetchone()
        if not row:
            raise KeyError(node_id)
        return NodeInstance.model_validate(row[0])

    def list_project_nodes(self, project_id: str) -> list[NodeInstance]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM workflow_nodes WHERE project_id = %s ORDER BY updated_at, id",
                (project_id,),
            ).fetchall()
        return [NodeInstance.model_validate(payload) for (payload,) in rows]

    def add_event(self, event: AuditEvent) -> AuditEvent:
        payload = event.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO workflow_events (id, project_id, node_instance_id, created_at, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (event.id, event.project_id, event.node_instance_id, event.created_at, Jsonb(payload)),
            )
        return event
