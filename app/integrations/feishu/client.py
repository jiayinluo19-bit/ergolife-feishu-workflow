import json
import os
from dataclasses import dataclass

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody


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

