import threading
from collections import deque
from .config import settings
from .models import StockState, CandidateRow

class RuntimeStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.states: dict[str, StockState] = {}
        self.last_candidates: list[CandidateRow] = []
        self.last_scan_summary: str = "준비"

    def ensure_state(self, code: str, name: str) -> StockState:
        with self.lock:
            if code not in self.states:
                self.states[code] = StockState(code=code, name=name, history=deque(maxlen=settings.history_size))
            elif name:
                self.states[code].name = name
            return self.states[code]

    def snapshot_candidates(self):
        with self.lock:
            return list(self.last_candidates)

    def set_candidates(self, rows):
        with self.lock:
            self.last_candidates = list(rows)

    def set_summary(self, summary: str):
        with self.lock:
            self.last_scan_summary = summary

    def get_summary(self) -> str:
        with self.lock:
            return self.last_scan_summary

store = RuntimeStore()