"""Role-aware product workbench service for the Feishu web app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config_loader import load_role_assignments
from ..domain.models import NodeDefinition, RoleAssignment
from ..repositories.directory_repository import DirectoryRepository
from ..repositories.lifecycle_repository import LifecycleRepository
from ..repositories.product_repository import ProductRecord, ProductRepository


@dataclass(frozen=True)
class ActorContext:
    open_id: str
    role: str | None
    department: str | None
    display_name: str
    demo: bool = False
    roles: tuple[str, ...] = ()


class ProductAccessService:
    def __init__(
        self,
        repository: ProductRepository,
        definitions: dict[str, NodeDefinition],
        roles: dict[str, RoleAssignment],
        *,
        demo_mode: bool = True,
        directory: DirectoryRepository | None = None,
        lifecycle_repository: LifecycleRepository | None = None,
    ) -> None:
        self.repository = repository
        self.definitions = definitions
        self.roles = roles
        self.demo_mode = demo_mode
        self.directory = directory
        self.lifecycle_repository = lifecycle_repository
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
                roles=(demo_role,),
            )
        if self.directory and open_id:
            user = self.directory.get_user(open_id)
            user_roles = tuple(role for role in self.directory.roles_for_user(open_id) if role in self.roles)
            if user:
                primary_role = user_roles[0] if user_roles else None
                return ActorContext(
                    open_id=open_id,
                    role=primary_role,
                    department=user.department_names[0] if user.department_names else (self.roles[primary_role].department if primary_role else None),
                    display_name=user.display_name,
                    roles=user_roles,
                )
        role = self._role_by_user.get(open_id)
        if role:
            assignment = self.roles[role]
            return ActorContext(open_id, role, assignment.department, assignment.display_name, roles=(role,))
        return ActorContext(open_id or "anonymous", None, None, "未识别用户", roles=())

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
        summary = {
            "total": len(products),
            "actionable": sum(item["access"]["can_advance"] for item in products),
            "overdue": sum(item["lifecycle"]["deadline_status"] == "overdue" for item in products),
            "due_soon": sum(item["lifecycle"]["deadline_status"] == "due_soon" for item in products),
        }
        return {
            "view": view,
            "actor": {
                "open_id": actor.open_id,
                "role": actor.role,
                "department": actor.department,
                "display_name": actor.display_name,
                "demo": actor.demo,
                "roles": list(actor.roles),
                "is_admin": bool(self.directory and self.directory.is_admin(open_id)),
            },
            "source": self.repository.last_source,
            "roles": self.available_roles(),
            "summary": summary,
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
        if not set(actor.roles).intersection(allowed_roles):
            raise PermissionError(f"当前角色不能操作 {definition.id}，负责人角色为 {definition.owner_role}")
        if not definition.next_nodes:
            raise ValueError("当前商品已经到达生命周期末端")
        next_node = definition.next_nodes[0]
        if self.lifecycle_repository:
            self.lifecycle_repository.ensure_product(
                product.id,
                product.lifecycle_node_code,
                self.definitions,
                {role: assignment.user_id for role, assignment in self.roles.items()},
            )
        updated = self.repository.advance(product.id, product.lifecycle_node_code, next_node)
        if self.lifecycle_repository:
            try:
                self.lifecycle_repository.record_advance(
                    product.id,
                    product.lifecycle_node_code,
                    next_node,
                    actor.open_id or actor.role or "unknown",
                    self.definitions,
                    {role: assignment.user_id for role, assignment in self.roles.items()},
                )
            except Exception:
                # The product-master update is already committed in another
                # database.  Read-side reconciliation repairs the lifecycle
                # projection on the next detail request without pretending
                # that the handoff itself failed.
                import logging

                logging.getLogger(__name__).exception(
                    "product advanced but lifecycle history could not be recorded: %s", product.id
                )
        return self._enrich(updated, actor)

    def _enrich(self, product: ProductRecord, actor: ActorContext) -> dict[str, Any]:
        definition = self.definitions.get(product.lifecycle_node_code)
        owner_members = self._members_for_role(definition.owner_role) if definition else []
        reviewer_members = self._members_for_role(definition.reviewer_role or definition.owner_role) if definition else []
        owner = owner_members[0] if owner_members else self.roles.get(definition.owner_role) if definition else None
        reviewer = reviewer_members[0] if reviewer_members else self.roles.get(definition.reviewer_role or definition.owner_role) if definition else None
        participant_roles = set()
        if definition:
            participant_roles.update(definition.collaborator_roles)
            participant_roles.update({definition.owner_role, definition.reviewer_role or definition.owner_role})
        index = self._ordered_codes.index(product.lifecycle_node_code) if product.lifecycle_node_code in self._ordered_codes else -1
        previous_code = self._ordered_codes[index - 1] if index > 0 else None
        next_code = definition.next_nodes[0] if definition and definition.next_nodes else None
        next_definition = self.definitions.get(next_code) if next_code else None
        next_owner_members = self._members_for_role(next_definition.owner_role) if next_definition else []
        next_owner = next_owner_members[0] if next_owner_members else self.roles.get(next_definition.owner_role) if next_definition else None
        deadline = self._deadline(product, definition)
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
                "next_owner_user_ids": [member.open_id for member in next_owner_members if member.open_id]
                or ([next_owner.user_id] if next_owner and next_owner.user_id else []),
                "next_owner_count": len(next_owner_members),
                **deadline,
            },
            "access": {
                "is_owner": bool(definition and definition.owner_role in actor.roles),
                "is_reviewer": bool(definition and definition.reviewer_role and definition.reviewer_role in actor.roles),
                "is_participant": bool(set(actor.roles).intersection(participant_roles)),
                "can_advance": bool(set(actor.roles).intersection(participant_roles) and next_code),
                "action_label": f"完成 {product.lifecycle_node_code} 并交接至 {next_code}" if next_code else "已完成",
            },
        }

    def _members_for_role(self, role: str | None):
        if not role:
            return []
        if self.directory:
            members = self.directory.members_for_role(role)
            if members:
                return members
        assignment = self.roles.get(role)
        if not assignment:
            return []
        # Keep the existing mock RoleAssignment compatible with the directory
        # member shape used by notifications and the UI.
        from ..repositories.directory_repository import DirectoryUser

        return [
            DirectoryUser(
                open_id=assignment.user_id,
                user_id=assignment.user_id,
                display_name=assignment.display_name,
                department_names=(assignment.department,),
            )
        ]

    @staticmethod
    def _deadline(product: ProductRecord, definition: NodeDefinition | None) -> dict[str, Any]:
        """Derive an MVP deadline from the product row's last update.

        ``product_market_parameters`` currently stores the current node but not
        a node history.  ``updated_at`` is therefore used as the entered-at
        approximation until the lifecycle event table is introduced.
        """
        sla_hours = int(definition.sla_hours if definition else 24)
        try:
            entered_at = datetime.fromisoformat(product.updated_at) if product.updated_at else datetime.now(timezone.utc)
        except ValueError:
            entered_at = datetime.now(timezone.utc)
        if entered_at.tzinfo is None:
            entered_at = entered_at.replace(tzinfo=timezone.utc)
        due_at = entered_at + timedelta(hours=sla_hours)
        remaining_hours = (due_at - datetime.now(timezone.utc)).total_seconds() / 3600
        if remaining_hours < 0:
            status = "overdue"
            label = f"已逾期 {abs(int(remaining_hours))} 小时"
        elif remaining_hours <= 24:
            status = "due_soon"
            label = f"剩余 {max(1, int(remaining_hours))} 小时"
        else:
            status = "normal"
            label = f"剩余 {max(1, int(remaining_hours))} 小时"
        return {
            "entered_at": entered_at.isoformat(),
            "due_at": due_at.isoformat(),
            "deadline_status": status,
            "deadline_label": label,
            "sla_hours": sla_hours,
        }
