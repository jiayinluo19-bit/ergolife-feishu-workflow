from .interfaces import Repository
from ..domain.models import AuditEvent, NodeInstance, ProductProject


class MemoryRepository(Repository):
    def __init__(self) -> None:
        self.projects: dict[str, ProductProject] = {}
        self.nodes: dict[str, NodeInstance] = {}
        self.events: list[AuditEvent] = []

    def save_project(self, project: ProductProject) -> ProductProject:
        self.projects[project.id] = project
        return project

    def get_project(self, project_id: str) -> ProductProject:
        return self.projects[project_id]

    def save_node(self, node: NodeInstance) -> NodeInstance:
        self.nodes[node.id] = node
        return node

    def delete_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)

    def get_node(self, node_id: str) -> NodeInstance:
        return self.nodes[node_id]

    def list_project_nodes(self, project_id: str) -> list[NodeInstance]:
        return [node for node in self.nodes.values() if node.project_id == project_id]

    def add_event(self, event: AuditEvent) -> AuditEvent:
        self.events.append(event)
        return event
