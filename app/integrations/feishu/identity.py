"""Feishu web-app sign-in and lightweight signed session support.

The bot already receives ``operator.open_id`` in card callbacks.  The web
workbench needs the same identity when opened from the Feishu app, so it uses
Feishu's in-app OAuth authorization-code flow and stores only a signed open_id
in an HttpOnly cookie.  Access tokens are never sent to the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen


class FeishuIdentityError(RuntimeError):
    pass


class FeishuIdentity:
    def __init__(self) -> None:
        self.app_id = os.getenv("FEISHU_APP_ID", "").strip()
        self.app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        self.public_base_url = os.getenv(
            "PUBLIC_BASE_URL", "https://ergolife-feishu-workflow-production.up.railway.app"
        ).rstrip("/")
        self.redirect_uri = os.getenv(
            "FEISHU_REDIRECT_URI", f"{self.public_base_url}/auth/feishu/callback"
        )
        self.session_secret = os.getenv("SESSION_SECRET", self.app_secret or "local-demo-session-secret")
        self._states: dict[str, tuple[float, str]] = {}

    def authorization_url(self, next_path: str = "/dashboard") -> str:
        if not self.app_id:
            raise FeishuIdentityError("未配置 FEISHU_APP_ID")
        state = secrets.token_urlsafe(24)
        safe_next_path = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/dashboard"
        self._states[state] = (time.time() + 300, safe_next_path)
        query = urlencode({"app_id": self.app_id, "redirect_uri": self.redirect_uri, "state": state})
        return f"https://open.feishu.cn/open-apis/authen/v1/authorize?{query}"

    def exchange_code(self, code: str, state: str) -> tuple[dict, str]:
        state_record = self._states.pop(state, None)
        if not state_record or state_record[0] < time.time():
            raise FeishuIdentityError("登录状态已失效，请重新登录")
        if not self.app_id or not self.app_secret:
            raise FeishuIdentityError("未配置飞书应用凭证")
        return self._exchange_user_code(code), state_record[1]

    def exchange_h5_code(self, code: str) -> dict:
        """Exchange ``tt.requestAuthCode`` output from the Feishu client."""
        if not code:
            raise FeishuIdentityError("飞书端内免登缺少 code")
        return self._exchange_user_code(code)

    def _exchange_user_code(self, code: str) -> dict:
        app_token = self._post_json(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        ).get("app_access_token")
        if not app_token:
            raise FeishuIdentityError("获取飞书 app_access_token 失败")
        data = self._post_json(
            "https://open.feishu.cn/open-apis/authen/v1/access_token",
            {"grant_type": "authorization_code", "code": code},
            headers={"Authorization": f"Bearer {app_token}"},
        )
        user = data.get("data") or data
        open_id = user.get("open_id")
        if not open_id:
            raise FeishuIdentityError("飞书登录返回中没有 open_id")
        # The auth endpoint supplies the identity, while the Contact API
        # supplies department and job information used by role rules.  A
        # missing Contact permission must not prevent a user from opening the
        # workbench; they can be mapped later by an administrator.
        try:
            profile = self._get_user_profile(app_token, str(open_id))
        except FeishuIdentityError:
            profile = {}
        if profile:
            user = {**user, **profile}
        return user

    def _get_user_profile(self, app_token: str, open_id: str) -> dict:
        data = self._get_json(
            "https://open.feishu.cn/open-apis/contact/v3/users/"
            + open_id
            + "?user_id_type=open_id&department_id_type=department_id",
            headers={"Authorization": f"Bearer {app_token}"},
        )
        profile = data.get("data", {}).get("user") or data.get("user") or {}
        if not isinstance(profile, dict):
            return {}
        department_path = profile.get("department_path") or []
        department_names = profile.get("department_names") or []
        department_ids = profile.get("department_ids") or []
        if department_path and isinstance(department_path, list):
            for item in department_path:
                if not isinstance(item, dict):
                    continue
                if item.get("department_id"):
                    department_ids.append(str(item["department_id"]))
                name = item.get("department_name")
                if isinstance(name, dict):
                    name = name.get("name") or name.get("zh_cn") or name.get("en_us")
                if name:
                    department_names.append(str(name))
        profile["department_ids"] = list(dict.fromkeys(str(item) for item in department_ids if item))
        profile["department_names"] = list(dict.fromkeys(str(item) for item in department_names if item))
        return profile

    def sign_session(self, open_id: str) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps({"open_id": open_id, "exp": int(time.time()) + 86400}, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        signature = hmac.new(self.session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def read_session(self, value: str | None) -> str | None:
        if not value or "." not in value:
            return None
        payload, signature = value.rsplit(".", 1)
        expected = hmac.new(self.session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            padded = payload + "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        except (ValueError, json.JSONDecodeError):
            return None
        if data.get("exp", 0) < time.time():
            return None
        return str(data.get("open_id")) if data.get("open_id") else None

    @staticmethod
    def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        request = UrlRequest(url, data=json.dumps(payload).encode(), headers=request_headers, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FeishuIdentityError(f"飞书登录接口调用失败: {exc}") from exc
        if body.get("code", 0) not in (0, "0"):
            raise FeishuIdentityError(f"飞书登录接口返回错误: {body.get('code')} {body.get('msg', '')}")
        return body

    @staticmethod
    def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
        request = UrlRequest(url, headers=headers or {}, method="GET")
        try:
            with urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FeishuIdentityError(f"飞书通讯录接口调用失败: {exc}") from exc
        if body.get("code", 0) not in (0, "0"):
            raise FeishuIdentityError(f"飞书通讯录接口返回错误: {body.get('code')} {body.get('msg', '')}")
        return body
