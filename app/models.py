from dataclasses import dataclass
from collections import deque

@dataclass
class TickSnapshot:
    ts: float
    price: int
    open_price: int
    high_price: int
    low_price: int
    volume: int
    trade_value: int
    change_rate: float

@dataclass
class StockState:
    code: str
    name: str
    history: deque
    last_alert_ts: float = 0.0
    source: str = "watchlist"

@dataclass
class CandidateRow:
    code: str
    name: str
    category: str
    price: int
    change_rate: float
    volume: int
    trade_value: int
    score: int
    near_high_pct: float
    volume_surge_ratio: float
    trade_surge_ratio: float
    reasons: list[str]
    plan: dict
    pattern: str = ""
    chart_note: str = ""
    buy_price: int = 0
    stop_price: int = 0
    target1_price: int = 0
    target2_price: int = 0
    signal_grade: str = ""
    priority_rank: int = 0
    news_summary: str = ""
    disclosure_summary: str = ""
