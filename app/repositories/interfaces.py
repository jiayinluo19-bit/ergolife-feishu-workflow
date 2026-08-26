from abc import ABC, abstractmethod

from ..domain.models import AuditEvent, NodeInstance, ProductProject


class Repository(ABC):
    @abstractmethod
    def save_project(self, project: ProductProject) -> ProductProject: ...

    @abstractmethod
    def get_project(self, project_id: str) -> ProductProject: ...

    @abstractmethod
    def save_node(self, node: NodeInstance) -> NodeInstance: ...

    @abstractmethod
    def get_node(self, node_id: str) -> NodeInstance: ...

    @abstractmethod
    def list_project_nodes(self, project_id: str) -> list[NodeInstance]: ...

    @abstractmethod
    def add_event(self, event: AuditEvent) -> AuditEvent: ...

