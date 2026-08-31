"""Persistence for the real product lifecycle projection and its history.

The product master lives in a different PostgreSQL database.  This repository
therefore deliberately stores ``product_id`` as an external text key instead
of adding a cross-database foreign key.  The product master remains the source
of the current projection; this database owns the lifecycle instances, node
occurrences and audit history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from ..domain.models import NodeDefinition

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LifecycleNodeRecord:
    id: str
    product_id: str
    definition_id: str
    occurrence: int
    sequence_no: int
    status: str
    owner_user_id: str
    reviewer_user_id: str
    started_at: str | None
    submitted_at: str | None
    completed_at: str | None
    events: list[dict[str, Any]]


@dataclass(frozen=True)
class LifecycleSnapshot:
    product_id: str
    current_node_id: str | None
    current_node_code: str
    nodes: list[LifecycleNodeRecord]


class LifecycleRepository:
    """Store formal lifecycle data in the workflow application database."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = (dsn or "").strip()
        if self.dsn:
            self.ensure_schema()

    @property
    def source(self) -> str:
        return "postgres" if self.dsn else "memory"

    def ensure_schema(self) -> None:
        if not self.dsn:
            return
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS product_lifecycle_instances (
                    product_id TEXT PRIMARY KEY,
                    lifecycle_version TEXT NOT NULL DEFAULT 'v1',
                    current_node_id TEXT,
                    current_node_code TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS product_lifecycle_instances_node_idx
                    ON product_lifecycle_instances (current_node_code, status);
                CREATE TABLE IF NOT EXISTS product_lifecycle_nodes (
                    id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL REFERENCES product_lifecycle_instances(product_id) ON DELETE CASCADE,
                    definition_id TEXT NOT NULL,
                    occurrence INTEGER NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    reviewer_user_id TEXT NOT NULL DEFAULT '',
                    started_at TIMESTAMPTZ,
                    submitted_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (product_id, definition_id, occurrence),
                    UNIQUE (product_id, sequence_no)
                );
                CREATE INDEX IF NOT EXISTS product_lifecycle_nodes_product_idx
                    ON product_lifecycle_nodes (product_id, sequence_no);
                CREATE TABLE IF NOT EXISTS product_lifecycle_events (
                    id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL REFERENCES product_lifecycle_instances(product_id) ON DELETE CASCADE,
                    node_id TEXT,
                    event_type TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                    idempotency_key TEXT UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS product_lifecycle_events_product_idx
                    ON product_lifecycle_events (product_id, created_at, id);
                CREATE INDEX IF NOT EXISTS product_lifecycle_events_node_idx
                    ON product_lifecycle_events (node_id, created_at, id);
                """
            )

    def ensure_product(
        self,
        product_id: str,
        current_node_code: str,
        definitions: dict[str, NodeDefinition],
        assignments: dict[str, str],
    ) -> None:
        """Create or reconcile a lifecycle from the product master projection.

        On first sight, preceding nodes are marked completed as a snapshot of
        the current projection, but no fake per-node timestamps or transition
        events are created.  If a prior write was interrupted after the
        product-master update, reconciliation records a system event and
        repairs the workflow projection.
        """
        if not self.dsn:
            return
        if current_node_code not in definitions:
            logger.warning("cannot initialize unknown lifecycle node %s for %s", current_node_code, product_id)
            return
        ordered = list(definitions)
        current_index = ordered.index(current_node_code)
        with psycopg.connect(self.dsn) as connection:
            row = connection.execute(
                "SELECT current_node_id, current_node_code FROM product_lifecycle_instances WHERE product_id = %s FOR UPDATE",
                (product_id,),
            ).fetchone()
            if not row:
                connection.execute(
                    """
                    INSERT INTO product_lifecycle_instances
                        (product_id, current_node_id, current_node_code, started_at)
                    VALUES (%s, NULL, %s, NULL)
                    """,
                    (product_id, current_node_code),
                )
                self._create_initial_snapshot(
                    connection, product_id, current_node_code, current_index, ordered, definitions, assignments
                )
                connection.commit()
                return
            if str(row[1]) == current_node_code:
                connection.commit()
                return
            self._reconcile_projection(
                connection,
                product_id,
                str(row[0]) if row[0] else None,
                str(row[1]),
                current_node_code,
                current_index,
                ordered,
                definitions,
                assignments,
            )
            connection.commit()

    def _create_initial_snapshot(
        self,
        connection: psycopg.Connection,
        product_id: str,
        current_node_code: str,
        current_index: int,
        ordered: list[str],
        definitions: dict[str, NodeDefinition],
        assignments: dict[str, str],
    ) -> None:
        current_node_id = self._insert_initial_snapshot(
            connection, product_id, current_node_code, current_index, ordered, definitions, assignments
        )
        connection.execute(
            "UPDATE product_lifecycle_instances SET current_node_id = %s, updated_at = NOW() WHERE product_id = %s",
            (current_node_id, product_id),
        )
        self._insert_event(
            connection,
            product_id,
            current_node_id,
            "lifecycle_initialized",
            "system:product-master",
            {
                "source": "product_market_parameters",
                "current_node_code": current_node_code,
                "inferred_prior_nodes": ordered[:current_index],
            },
            f"initialize:{product_id}",
        )

    def _insert_initial_snapshot(
        self,
        connection: psycopg.Connection,
        product_id: str,
        current_node_code: str,
        current_index: int,
        ordered: list[str],
        definitions: dict[str, NodeDefinition],
        assignments: dict[str, str],
    ) -> str:
        # This helper is called only after the instance row exists.  It is
        # separated to keep the occurrence/sequence rules in one place.
        current_node_id = ""
        for sequence_no, definition_id in enumerate(ordered, start=1):
            definition = definitions[definition_id]
            node_id = f"LCN-{uuid4().hex[:12].upper()}"
            if definition_id == current_node_code:
                current_node_id = node_id
            owner = assignments.get(definition.owner_role, "")
            reviewer = assignments.get(definition.reviewer_role or definition.owner_role, "")
            status = "completed" if sequence_no - 1 < current_index else "in_progress" if definition_id == current_node_code else "pending"
            connection.execute(
                """
                INSERT INTO product_lifecycle_nodes
                    (id, product_id, definition_id, occurrence, sequence_no, status, owner_user_id, reviewer_user_id)
                VALUES (%s, %s, %s, 1, %s, %s, %s, %s)
                """,
                (node_id, product_id, definition_id, sequence_no, status, owner, reviewer),
            )
        return current_node_id

    def _reconcile_projection(
        self,
        connection: psycopg.Connection,
        product_id: str,
        previous_node_id: str | None,
        previous_code: str,
        current_code: str,
        current_index: int,
        ordered: list[str],
        definitions: dict[str, NodeDefinition],
        assignments: dict[str, str],
    ) -> None:
        current_row = connection.execute(
            "SELECT id FROM product_lifecycle_nodes WHERE product_id = %s AND definition_id = %s AND status != 'completed' ORDER BY sequence_no DESC LIMIT 1",
            (product_id, current_code),
        ).fetchone()
        current_node_id = str(current_row[0]) if current_row else self._create_occurrence(
            connection, product_id, current_code, current_index, definitions, assignments
        )
        if previous_node_id:
            connection.execute(
                "UPDATE product_lifecycle_nodes SET status = 'completed', completed_at = COALESCE(completed_at, NOW()), updated_at = NOW() WHERE id = %s",
                (previous_node_id,),
            )
        connection.execute(
            "UPDATE product_lifecycle_nodes SET status = 'in_progress', updated_at = NOW() WHERE id = %s",
            (current_node_id,),
        )
        connection.execute(
            """
            UPDATE product_lifecycle_instances
               SET current_node_id = %s, current_node_code = %s, status = 'active', updated_at = NOW()
             WHERE product_id = %s
            """,
            (current_node_id, current_code, product_id),
        )
        self._insert_event(
            connection,
            product_id,
            current_node_id,
            "projection_reconciled",
            "system:product-master",
            {"previous_node_code": previous_code, "current_node_code": current_code},
            f"reconcile:{product_id}:{current_node_id}",
        )

    def _create_occurrence(
        self,
        connection: psycopg.Connection,
        product_id: str,
        definition_id: str,
        sequence_hint: int,
        definitions: dict[str, NodeDefinition],
        assignments: dict[str, str],
    ) -> str:
        definition = definitions[definition_id]
        row = connection.execute(
            "SELECT COALESCE(MAX(occurrence), 0) + 1, COALESCE(MAX(sequence_no), 0) + 1 FROM product_lifecycle_nodes WHERE product_id = %s",
            (product_id,),
        ).fetchone()
        occurrence, sequence_no = int(row[0]), int(row[1])
        node_id = f"LCN-{uuid4().hex[:12].upper()}"
        connection.execute(
            """
            INSERT INTO product_lifecycle_nodes
                (id, product_id, definition_id, occurrence, sequence_no, status, owner_user_id, reviewer_user_id)
            VALUES (%s, %s, %s, %s, %s, 'in_progress', %s, %s)
            """,
            (
                node_id,
                product_id,
                definition_id,
                occurrence,
                max(sequence_no, sequence_hint),
                assignments.get(definition.owner_role, ""),
                assignments.get(definition.reviewer_role or definition.owner_role, ""),
            ),
        )
        return node_id

    def record_advance(
        self,
        product_id: str,
        expected_node_code: str,
        next_node_code: str,
        actor_user_id: str,
        definitions: dict[str, NodeDefinition],
        assignments: dict[str, str],
    ) -> None:
        """Record a successful product-master transition in one DB transaction."""
        if not self.dsn:
            return
        with psycopg.connect(self.dsn) as connection:
            instance = connection.execute(
                "SELECT current_node_id, current_node_code FROM product_lifecycle_instances WHERE product_id = %s FOR UPDATE",
                (product_id,),
            ).fetchone()
            if not instance:
                connection.rollback()
                raise RuntimeError(f"商品 {product_id} 尚未初始化生命周期")
            if str(instance[1]) != expected_node_code:
                connection.rollback()
                raise RuntimeError("生命周期历史与商品当前节点不一致，正在等待同步")
            current_node_id = str(instance[0]) if instance[0] else None
            if current_node_id:
                connection.execute(
                    "UPDATE product_lifecycle_nodes SET status = 'completed', completed_at = NOW(), updated_at = NOW() WHERE id = %s",
                    (current_node_id,),
                )
            next_row = connection.execute(
                "SELECT id FROM product_lifecycle_nodes WHERE product_id = %s AND definition_id = %s AND status != 'completed' ORDER BY sequence_no DESC LIMIT 1",
                (product_id, next_node_code),
            ).fetchone()
            if next_row:
                next_node_id = str(next_row[0])
                connection.execute(
                    "UPDATE product_lifecycle_nodes SET status = 'in_progress', updated_at = NOW() WHERE id = %s",
                    (next_node_id,),
                )
            else:
                next_node_id = self._create_occurrence(
                    connection,
                    product_id,
                    next_node_code,
                    len(definitions),
                    definitions,
                    assignments,
                )
            connection.execute(
                """
                UPDATE product_lifecycle_instances
                   SET current_node_id = %s, current_node_code = %s, status = 'active', updated_at = NOW()
                 WHERE product_id = %s
                """,
                (next_node_id, next_node_code, product_id),
            )
            transition_key = f"advance:{product_id}:{current_node_id}:{next_node_code}"
            self._insert_event(
                connection,
                product_id,
                current_node_id,
                "node_advanced",
                actor_user_id,
                {"from_node": expected_node_code, "to_node": next_node_code, "next_node_id": next_node_id},
                transition_key,
            )
            connection.commit()

    def snapshot(self, product_id: str) -> LifecycleSnapshot | None:
        if not self.dsn:
            return None
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            instance = connection.execute(
                "SELECT current_node_id, current_node_code FROM product_lifecycle_instances WHERE product_id = %s",
                (product_id,),
            ).fetchone()
            if not instance:
                return None
            rows = connection.execute(
                """
                SELECT id, product_id, definition_id, occurrence, sequence_no, status,
                       owner_user_id, reviewer_user_id, started_at, submitted_at, completed_at
                FROM product_lifecycle_nodes
                WHERE product_id = %s ORDER BY sequence_no, id
                """,
                (product_id,),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT node_id, event_type, actor_user_id, detail, created_at FROM product_lifecycle_events WHERE product_id = %s ORDER BY created_at, id",
                (product_id,),
            ).fetchall()
        events_by_node: dict[str, list[dict[str, Any]]] = {}
        for node_id, event_type, actor, detail, created_at in event_rows:
            events_by_node.setdefault(str(node_id) if node_id else "", []).append(
                {
                    "type": str(event_type),
                    "actor": str(actor),
                    "detail": detail or {},
                    "created_at": self._format_datetime(created_at),
                }
            )
        nodes = [
            LifecycleNodeRecord(
                id=str(row[0]),
                product_id=str(row[1]),
                definition_id=str(row[2]),
                occurrence=int(row[3]),
                sequence_no=int(row[4]),
                status=str(row[5]),
                owner_user_id=str(row[6] or ""),
                reviewer_user_id=str(row[7] or ""),
                started_at=self._format_datetime(row[8]),
                submitted_at=self._format_datetime(row[9]),
                completed_at=self._format_datetime(row[10]),
                events=events_by_node.get(str(row[0]), []),
            )
            for row in rows
        ]
        return LifecycleSnapshot(
            product_id=product_id,
            current_node_id=str(instance[0]) if instance[0] else None,
            current_node_code=str(instance[1]),
            nodes=nodes,
        )

    def _insert_event(
        self,
        connection: psycopg.Connection,
        product_id: str,
        node_id: str | None,
        event_type: str,
        actor_user_id: str,
        detail: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO product_lifecycle_events
                (id, product_id, node_id, event_type, actor_user_id, detail, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (f"LCE-{uuid4().hex[:12].upper()}", product_id, node_id, event_type, actor_user_id, Jsonb(detail), idempotency_key),
        )

    @staticmethod
    def _format_datetime(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
