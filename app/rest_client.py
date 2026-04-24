import time
import requests
from . import websocket_client  # noqa: F401
from ..config import settings
from ..logger import get_logger

logger = get_logger("rest_client")

class KISRestClient:
    def __init__(self):
        self.app_key = settings.kis_app_key
        self.app_secret = settings.kis_app_secret
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY / KIS_APP_SECRET 이 필요합니다.")

        self.base_url = (
            "https://openapivts.koreainvestment.com:29443"
            if settings.kis_use_mock else
            "https://openapi.koreainvestment.com:9443"
        )
        self.access_token = None
        self.token_expire_ts = 0.0

    def issue_access_token(self) -> str:
        url = f"{self.base_url}/oauth2/tokenP"
        headers = {"content-type": "application/json; charset=UTF-8"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"접근토큰 발급 실패: {data}")
        self.access_token = token
        self.token_expire_ts = time.time() + 23 * 3600
        logger.info("REST 접근토큰 발급 완료")
        return token

    def ensure_token(self) -> str:
        if (not self.access_token) or (time.time() >= self.token_expire_ts):
            return self.issue_access_token()
        return self.access_token

    def issue_approval_key(self) -> str:
        url = f"{self.base_url}/oauth2/Approval"
        headers = {"content-type": "application/json; charset=UTF-8"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        approval_key = data.get("approval_key")
        if not approval_key:
            raise RuntimeError(f"실시간 접속키 발급 실패: {data}")
        logger.info("WebSocket approval key 발급 완료")
        return approval_key

    def inquire_price(self, code: str) -> dict:
        token = self.ensure_token()
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "content-type": "application/json; charset=UTF-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": "FHKST01010100",
        }
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rt_cd = data.get("rt_cd")
        if rt_cd not in (None, "0"):
            raise RuntimeError(f"현재가 조회 실패: {data}")
        return data.get("output", {})