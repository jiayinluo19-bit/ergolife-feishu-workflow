import json
import os
from dataclasses import dataclass

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.api.contact.v3 import (
    BatchGetIdUserRequest,
    BatchGetIdUserRequestBody,
    FindByDepartmentUserRequest,
    ListDepartmentRequest,
)


class FeishuNotConfiguredError(RuntimeError):
    """Raised when the app credentials are not configured in the runtime."""


@dataclass(frozen=True)
class FeishuSettings:
    app_id: str
    app_secret: str
    verification_token: str = ""
    encrypt_key: str = ""

    @classmethod
    def from_env(cls) -> "FeishuSettings":
        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
        if not app_id or not app_secret:
            raise FeishuNotConfiguredError(
                "FEISHU_APP_ID and FEISHU_APP_SECRET must be configured"
            )
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", ""),
            encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", ""),
        )


class FeishuOpenAPI:
    def __init__(self, settings: FeishuSettings | None = None) -> None:
        self.settings = settings or FeishuSettings.from_env()
        self.client = (
            lark.Client.builder()
            .app_id(self.settings.app_id)
            .app_secret(self.settings.app_secret)
            .build()
        )

    def resolve_user_open_id_by_email(self, email: str) -> str:
        request = (
            BatchGetIdUserRequest.builder()
            .user_id_type("open_id")
            .request_body(BatchGetIdUserRequestBody.builder().emails([email]).build())
            .build()
        )
        response = self.client.contact.v3.user.batch_get_id(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu resolve user failed: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
            )
        users = response.data.user_list if response.data else []
        if not users or not users[0].user_id:
            raise RuntimeError("Feishu returned no user for the configured email")
        return users[0].user_id

    def list_directory_users(self) -> list[dict]:
        """Read all active directory users grouped by their departments.

        Feishu exposes users by department, so the sync first asks for the
        complete department tree and then walks each department's paginated
        member list.  Duplicate users are merged before returning.
        """
        departments = []
        page_token = ""
        while True:
            builder = (
                ListDepartmentRequest.builder()
                .user_id_type("open_id")
                .department_id_type("department_id")
                .parent_department_id("0")
                .fetch_child(True)
                .page_size(50)
            )
            if page_token:
                builder = builder.page_token(page_token)
            response = self.client.contact.v3.department.list(builder.build())
            if not response.success():
                raise RuntimeError(
                    f"Feishu list departments failed: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
                )
            body = response.data
            departments.extend(body.items if body and body.items else [])
            if not body or not body.has_more:
                break
            page_token = body.page_token or ""
            if not page_token:
                break

        users: dict[str, dict] = {}
        for department in departments:
            department_id = str(department.department_id or "")
            if not department_id:
                continue
            page_token = ""
            while True:
                builder = (
                    FindByDepartmentUserRequest.builder()
                    .user_id_type("open_id")
                    .department_id_type("department_id")
                    .department_id(department_id)
                    .page_size(50)
                )
                if page_token:
                    builder = builder.page_token(page_token)
                response = self.client.contact.v3.user.find_by_department(builder.build())
                if not response.success():
                    raise RuntimeError(
                        f"Feishu list users failed: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
                    )
                body = response.data
                for user in body.items if body and body.items else []:
                    open_id = str(user.open_id or "")
                    if not open_id:
                        continue
                    item = users.setdefault(
                        open_id,
                        {
                            "open_id": open_id,
                            "user_id": user.user_id,
                            "name": user.name or user.en_name,
                            "en_name": user.en_name,
                            "email": user.email or user.enterprise_email,
                            "job_title": user.job_title,
                            "department_ids": [],
                            "department_names": [],
                            "is_frozen": bool(user.is_frozen),
                            "is_tenant_manager": bool(user.is_tenant_manager),
                        },
                    )
                    if department_id not in item["department_ids"]:
                        item["department_ids"].append(department_id)
                    department_name = str(department.name or "").strip()
                    if department_name and department_name not in item["department_names"]:
                        item["department_names"].append(department_name)
                if not body or not body.has_more:
                    break
                page_token = body.page_token or ""
                if not page_token:
                    break
        return list(users.values())

    def send_text(self, receive_id: str, text: str, receive_id_type: str = "open_id") -> str:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu send message failed: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
            )
        return response.data.message_id if response.data else ""

    def send_interactive_card(
        self, receive_id: str, card: dict, receive_id_type: str = "open_id"
    ) -> str:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu send card failed: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
            )
        return response.data.message_id if response.data else ""
