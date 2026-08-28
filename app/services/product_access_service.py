"""Role-aware product workbench service for the Feishu web app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config_loader import load_role_assignments
from ..domain.models import NodeDefinition, RoleAssignment
from ..repositories.product_repository import ProductRecord, ProductRepository


@dataclass(frozen=True)
class ActorContext:
    open_id: str
    role: str | None
    department: str | None
    display_name: str
    demo: bool = False


class ProductAccessService:
    def __init__(
        self,
        repository: ProductRepository,
        definitions: dict[str, NodeDefinition],
        roles: dict[str, RoleAssignment],
        *,
        demo_mode: bool = True,
    ) -> None:
        self.repository = repository
        self.definitions = definitions
        self.roles = roles
        self.demo_mode = demo_mode
        self._ordered_codes = list(definitions)
        self._role_by_user = {assignment.user_id: role for role, assignment in roles.items()}

    def resolve_actor(self, open_id: str | None, demo_role: str | None = None) -> ActorContext:
        open_id = (open_id or "").strip()
        if self.demo_mode and demo_role in self.roles:
            assignment = self.roles[demo_role]
            return ActorContext(
                open_id=assignment.user_id,
                role=demo_role,
                department=assignment.department,
                display_name=assignment.display_name,
                demo=True,
            )
        role = self._role_by_user.get(open_id)
        if role:
            assignment = self.roles[role]
            return ActorContext(open_id, role, assignment.department, assignment.display_name)
        return ActorContext(open_id or "anonymous", None, None, "未识别用户")

    def available_roles(self) -> list[dict[str, str]]:
        return [
            {
                "role": role,
                "department": assignment.department,
                "display_name": assignment.display_name,
                "user_id": assignment.user_id,
            }
            for role, assignment in self.roles.items()
        ]

    def list_products(
        self,
        *,
        view: str = "mine",
        open_id: str | None = None,
        demo_role: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        actor = self.resolve_actor(open_id, demo_role)
        products = [self._enrich(product, actor) for product in self.repository.list_active(limit)]
        if view == "mine":
            products = [item for item in products if item["access"]["is_owner"]]
        elif view == "participating":
            products = [item for item in products if item["access"]["is_participant"]]
        elif view != "all":
            raise ValueError("view 必须是 mine、participating 或 all")
        return {
            "view": view,
            "actor": {
                "open_id": actor.open_id,
                "role": actor.role,
                "department": actor.department,
                "display_name": actor.display_name,
                "demo": actor.demo,
            },
            "source": self.repository.last_source,
            "roles": self.available_roles(),
            "products": products,
        }

    def advance_product(
        self,
        product_id: str,
        *,
        open_id: str | None = None,
        demo_role: str | None = None,
    ) -> dict[str, Any]:
        actor = self.resolve_actor(open_id, demo_role)
        product = next((item for item in self.repository.list_active(2000) if item.id == product_id), None)
        if product is None:
            raise KeyError(product_id)
        definition = self.definitions.get(product.lifecycle_node_code)
        if definition is None:
            raise ValueError(f"未识别生命周期节点: {product.lifecycle_node_code}")
        allowed_roles = {definition.owner_role, definition.reviewer_role or definition.owner_role, *definition.collaborator_roles}
        if actor.role not in allowed_roles:
            raise PermissionError(f"当前角色不能操作 {definition.id}，负责人角色为 {definition.owner_role}")
        if not definition.next_nodes:
            raise ValueError("当前商品已经到达生命周期末端")
        next_node = definition.next_nodes[0]
        updated = self.repository.advance(product.id, product.lifecycle_node_code, next_node)
        return self._enrich(updated, actor)

    def _enrich(self, product: ProductRecord, actor: ActorContext) -> dict[str, Any]:
        definition = self.definitions.get(product.lifecycle_node_code)
        owner = self.roles.get(definition.owner_role) if definition else None
        reviewer = self.roles.get(definition.reviewer_role or definition.owner_role) if definition else None
        participant_roles = set()
        if definition:
            participant_roles.update(definition.collaborator_roles)
            participant_roles.update({definition.owner_role, definition.reviewer_role or definition.owner_role})
        index = self._ordered_codes.index(product.lifecycle_node_code) if product.lifecycle_node_code in self._ordered_codes else -1
        previous_code = self._ordered_codes[index - 1] if index > 0 else None
        next_code = definition.next_nodes[0] if definition and definition.next_nodes else None
        next_definition = self.definitions.get(next_code) if next_code else None
        next_owner = self.roles.get(next_definition.owner_role) if next_definition else None
        return {
            **product.as_dict(),
            "lifecycle": {
                "node_code": product.lifecycle_node_code,
                "node_name": definition.name if definition else "未配置节点",
                "stage": definition.stage if definition else "未配置阶段",
                "owner_role": definition.owner_role if definition else None,
                "owner_department": owner.department if owner else None,
                "owner_name": owner.display_name if owner else None,
                "reviewer_role": definition.reviewer_role if definition else None,
                "reviewer_name": reviewer.display_name if reviewer else None,
                "previous_code": previous_code,
                "previous_name": self.definitions[previous_code].name if previous_code else None,
                "next_code": next_code,
                "next_name": self.definitions[next_code].name if next_code in self.definitions else None,
                "next_owner_role": next_definition.owner_role if next_definition else None,
                "next_owner_user_id": next_owner.user_id if next_owner else None,
                "next_owner_name": next_owner.display_name if next_owner else None,
            },
            "access": {
                "is_owner": bool(actor.role and definition and actor.role == definition.owner_role),
                "is_reviewer": bool(actor.role and definition and actor.role == definition.reviewer_role),
                "is_participant": bool(actor.role and actor.role in participant_roles),
                "can_advance": bool(actor.role and actor.role in participant_roles and next_code),
                "action_label": f"完成 {product.lifecycle_node_code} 并交接至 {next_code}" if next_code else "已完成",
            },
        }
