from app.repositories.directory_repository import DirectoryRepository


def test_directory_maps_one_user_to_multiple_roles_without_env_vars():
    directory = DirectoryRepository(
        role_rules={"产品部": "product_manager", "项目办公室": "quality_reviewer"}
    )
    directory.upsert_user(
        open_id="ou_employee_1",
        user_id="user_1",
        display_name="张三",
        department_names=["产品部", "项目办公室"],
    )

    assert directory.roles_for_user("ou_employee_1") == ["product_manager", "quality_reviewer"]
    assert [item.display_name for item in directory.members_for_role("product_manager")] == ["张三"]


def test_directory_replaces_auto_membership_when_department_changes():
    directory = DirectoryRepository(role_rules={"产品部": "product_manager", "采购部": "procurement_owner"})
    directory.upsert_user(open_id="ou_employee_1", display_name="张三", department_names=["产品部"])
    directory.upsert_user(open_id="ou_employee_1", display_name="张三", department_names=["采购部"])

    assert directory.roles_for_user("ou_employee_1") == ["procurement_owner"]
    assert directory.members_for_role("product_manager") == []


def test_manual_roles_override_department_and_can_be_restored():
    directory = DirectoryRepository(
        role_rules={"产品部": "product_manager"},
        known_roles=["product_manager", "quality_reviewer"],
    )
    directory.upsert_user(open_id="ou_employee_1", display_name="张三", department_names=["产品部"])
    directory.set_manual_roles("ou_employee_1", ["quality_reviewer"])
    assert directory.roles_for_user("ou_employee_1") == ["quality_reviewer"]

    directory.clear_manual_roles("ou_employee_1")
    assert directory.roles_for_user("ou_employee_1") == ["product_manager"]
