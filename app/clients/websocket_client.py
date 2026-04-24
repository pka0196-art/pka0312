import json
import threading
import websocket
from ..config import settings
from ..logger import get_logger

logger = get_logger("websocket")

class KISWebSocketClient:
    """
    V5 실시간형 템플릿.
    approval_key 발급은 구현되어 있고, 실제 구독 payload는 KIS 계정/시장구분에 따라
    조정이 필요할 수 있어서 기본형만 넣어둠.
    """
    def __init__(self, approval_key: str, on_message):
        self.approval_key = approval_key
        self.on_message_handler = on_message
        self.ws = None
        self.thread = None
        self.url = settings.kis_ws_url_mock if settings.kis_use_mock else settings.kis_ws_url_real

    def _on_open(self, ws):
        logger.info("WebSocket 연결 성공")
        for symbol in settings.ws_symbols:
            payload = {
                "header": {
                    "approval_key": self.approval_key,
                    "custtype": "P",
                    "tr_type": settings.ws_tr_type,
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": settings.ws_tr_id,
                        "tr_key": symbol,
                    }
                },
            }
            ws.send(json.dumps(payload))
            logger.info("실시간 구독 요청 전송: %s", symbol)

    def _on_message(self, ws, message):
        self.on_message_handler(message)

    def _on_error(self, ws, error):
        logger.warning("WebSocket 오류: %s", error)

    def _on_close(self, ws, code, msg):
        logger.info("WebSocket 종료: %s / %s", code, msg)

    def start(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.ws:
            self.ws.close()