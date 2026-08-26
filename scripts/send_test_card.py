import os

from dotenv import load_dotenv

from app.integrations.feishu.cards import task_assignment_card
from app.integrations.feishu.client import FeishuOpenAPI


def main() -> None:
    load_dotenv()
    receive_id = os.environ["FEISHU_TEST_RECEIVE_ID"]
    receive_id_type = os.getenv("FEISHU_RECEIVE_ID_TYPE", "open_id")
    api = FeishuOpenAPI()
    message_id = api.send_interactive_card(
        receive_id,
        task_assignment_card(
            project_id="PRJ-MOCK-001",
            node_instance_id="NODE-P01-MOCK",
            product_name="ERGOLIFE 人体工学办公椅 X1",
            node_name="P01 市场机会发现",
            owner_name="测试产品经理",
        ),
        receive_id_type=receive_id_type,
    )
    print(f"Feishu message sent: {message_id}")


if __name__ == "__main__":
    main()

