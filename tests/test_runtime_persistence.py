import app.runtime as runtime_module
from app.repositories.memory_repository import MemoryRepository


def _copy(model):
    return type(model).model_validate(model.model_dump(mode="json"))


class DetachedMemoryRepository(MemoryRepository):
    """Mimic a database repository that returns new model instances."""

    def save_project(self, project):
        self.projects[project.id] = _copy(project)
        return project

    def get_project(self, project_id):
        return _copy(self.projects[project_id])

    def save_node(self, node):
        self.nodes[node.id] = _copy(node)
        return node

    def get_node(self, node_id):
        return _copy(self.nodes[node_id])

    def list_project_nodes(self, project_id):
        return [_copy(node) for node in self.nodes.values() if node.project_id == project_id]

    def add_event(self, event):
        self.events.append(_copy(event))
        return event


def test_demo_seed_checkpoints_work_with_detached_repository(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("WORKFLOW_REPOSITORY", "memory")
    monkeypatch.setattr(runtime_module, "MemoryRepository", DetachedMemoryRepository)

    runtime = runtime_module.WorkflowRuntime()
    projects = {project["id"]: project for project in runtime.dashboard_data()}

    assert projects["PRJ-MOCK-001"]["current_node_id"] == "P01"
    assert projects["PRJ-MOCK-002"]["current_node_id"] == "P05"
    assert projects["PRJ-MOCK-002"]["completed"] == 4
    assert projects["PRJ-MOCK-003"]["current_node_id"] == "P12"
    assert projects["PRJ-MOCK-003"]["completed"] == 11
