"""Small adapter for the ecommerce product master database.

The workflow database and the ecommerce database are intentionally separate
connections.  This adapter only touches ``product_market_parameters`` so the
Feishu MVP can be introduced without coupling the workflow tables to the
larger replenishment schema.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductRecord:
    id: str
    sku: str
    country_code: str
    amazon_sku: str | None
    product_name: str
    category: str | None
    store: str | None
    lifecycle_node_code: str
    is_active: bool
    updated_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sku": self.sku,
            "country_code": self.country_code,
            "amazon_sku": self.amazon_sku,
            "product_name": self.product_name,
            "category": self.category,
            "store": self.store,
            "lifecycle_node_code": self.lifecycle_node_code,
            "is_active": self.is_active,
            "updated_at": self.updated_at,
        }


class ProductRepository:
    """Read/update the product master, with deterministic demo fallback."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = (dsn or os.getenv("PRODUCT_DATABASE_URL") or "").strip()
        self.last_source = "mock"
        self._mock_store = self._build_mock_products()

    def list_active(self, limit: int = 500) -> list[ProductRecord]:
        if not self.dsn:
            self.last_source = "mock"
            return list(self._mock_store)
        try:
            with psycopg.connect(self.dsn, connect_timeout=5, autocommit=True) as connection:
                rows = connection.execute(
                    """
                    SELECT id::text, sku, country_code, amazon_sku,
                           COALESCE(product_name, name_zh, name_en, sku) AS product_name,
                           category, store, lifecycle_node_code, is_active, updated_at
                    FROM product_market_parameters
                    WHERE is_active = TRUE
                    ORDER BY sku, country_code, amazon_sku NULLS LAST
                    LIMIT %s
                    """,
                    (max(1, min(limit, 2000)),),
                ).fetchall()
            self.last_source = "postgres"
            return [self._from_row(row) for row in rows]
        except Exception as exc:  # keep the Feishu board available during DB rollout
            self.last_source = "mock-fallback"
            logger.warning("product database unavailable, using demo records: %s", exc)
            return list(self._mock_store)

    def advance(self, product_id: str, expected_node: str, next_node: str) -> ProductRecord:
        if not self.dsn:
            for index, product in enumerate(self._mock_store):
                if product.id == product_id and product.lifecycle_node_code == expected_node:
                    self._mock_store[index] = replace(product, lifecycle_node_code=next_node)
                    return self._mock_store[index]
            raise RuntimeError("商品状态已被其他人更新，请刷新后重试")
        with psycopg.connect(self.dsn, connect_timeout=5, autocommit=True) as connection:
            row = connection.execute(
                """
                UPDATE product_market_parameters
                   SET lifecycle_node_code = %s, updated_at = NOW()
                 WHERE id = %s::uuid AND lifecycle_node_code = %s AND is_active = TRUE
             RETURNING id::text, sku, country_code, amazon_sku,
                       COALESCE(product_name, name_zh, name_en, sku) AS product_name,
                       category, store, lifecycle_node_code, is_active, updated_at
                """,
                (next_node, product_id, expected_node),
            ).fetchone()
        if not row:
            raise RuntimeError("商品状态已被其他人更新，请刷新后重试")
        self.last_source = "postgres"
        return self._from_row(row)

    @staticmethod
    def _from_row(row: tuple[Any, ...]) -> ProductRecord:
        return ProductRecord(
            id=str(row[0]),
            sku=str(row[1] or ""),
            country_code=str(row[2] or ""),
            amazon_sku=str(row[3]) if row[3] is not None else None,
            product_name=str(row[4] or row[1] or "未命名商品"),
            category=str(row[5]) if row[5] is not None else None,
            store=str(row[6]) if row[6] is not None else None,
            lifecycle_node_code=str(row[7] or "P01"),
            is_active=bool(row[8]),
            updated_at=ProductRepository._format_datetime(row[9]),
        )

    @staticmethod
    def _format_datetime(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _build_mock_products() -> list[ProductRecord]:
        rows = [
            ("demo-70030", "70030-2TK", "US", "70030US0109HXD-K", "可调节阻力登山机（黑灰色）", "健身器材", "Amazon US", "P01"),
            ("demo-70012-uk", "70012-2", "UK", "60012UK04HXD", "握力器（白色）", "健身器材", "Amazon UK", "P05"),
            ("demo-70034", "70034-2TM", "US", "70034US01HXD-M", "ERGOLIFE 可调节壶铃", "健身器材", "Amazon US", "P06"),
            ("demo-70001", "70001-8", "CA", "7808US10HXD", "8LB 浸塑人体工学哑铃", "健身器材", "Amazon CA", "P12"),
            ("demo-70012-us", "70012-2", "US", "70012US04HXD", "握力器（白色）", "健身器材", "Amazon US", "P18"),
            ("demo-70027", "70027-1", "EU", "70027DE07HXD", "蹦床（粉色）", "健身器材", "Amazon EU", "P22"),
        ]
        return [
            ProductRecord(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], True, None)
            for row in rows
        ]
