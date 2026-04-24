from dataclasses import dataclass, field
from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env", override=True)

@dataclass
class Settings:
    base_dir: Path = BASE_DIR
    log_dir: Path = BASE_DIR / "logs"
    watchlist_file: Path = BASE_DIR / "watchlist.txt"
    market_all_file: Path = BASE_DIR / "market_all.txt"

    app_mode: str = os.getenv("APP_MODE", "rest")
    poll_interval_sec: int = int(os.getenv("POLL_INTERVAL_SEC", "8"))
    history_size: int = int(os.getenv("HISTORY_SIZE", "20"))
    alert_score_threshold: int = int(os.getenv("ALERT_SCORE_THRESHOLD", "35"))
    alert_cooldown_sec: int = int(os.getenv("ALERT_COOLDOWN_SEC", "300"))

    kis_app_key: str = os.getenv("KIS_APP_KEY", "")
    kis_app_secret: str = os.getenv("KIS_APP_SECRET", "")
    kis_use_mock: bool = os.getenv("KIS_USE_MOCK", "true").lower() == "true"

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    enable_market_scanner: bool = os.getenv("ENABLE_MARKET_SCANNER", "true").lower() == "true"
    market_batch_size: int = int(os.getenv("MARKET_BATCH_SIZE", "80"))
    auto_candidate_count: int = int(os.getenv("AUTO_CANDIDATE_COUNT", "20"))
    pin_watchlist_always: bool = os.getenv("PIN_WATCHLIST_ALWAYS", "true").lower() == "true"
    market_refresh_cycles: int = int(os.getenv("MARKET_REFRESH_CYCLES", "1"))
    min_price_filter: int = int(os.getenv("MIN_PRICE_FILTER", "1000"))
    max_price_filter: int = int(os.getenv("MAX_PRICE_FILTER", "500000"))
    min_base_change_filter: float = float(os.getenv("MIN_BASE_CHANGE_FILTER", "-1.0"))

    min_change_rate_for_alert: float = float(os.getenv("MIN_CHANGE_RATE_FOR_ALERT", "0.3"))
    min_near_high_pct_for_alert: float = float(os.getenv("MIN_NEAR_HIGH_PCT_FOR_ALERT", "97.0"))
    min_volume_surge_ratio_for_alert: float = float(os.getenv("MIN_VOLUME_SURGE_RATIO_FOR_ALERT", "1.6"))
    min_trade_surge_ratio_for_alert: float = float(os.getenv("MIN_TRADE_SURGE_RATIO_FOR_ALERT", "1.4"))

    realtime_enabled: bool = os.getenv("REALTIME_ENABLED", "false").lower() == "true"
    kis_ws_url_mock: str = os.getenv("KIS_WS_URL_MOCK", "ws://ops.koreainvestment.com:31000")
    kis_ws_url_real: str = os.getenv("KIS_WS_URL_REAL", "ws://ops.koreainvestment.com:21000")
    ws_symbols: list[str] = field(default_factory=lambda: [x.strip() for x in os.getenv("WS_SYMBOLS", "005930,000660").split(",") if x.strip()])
    ws_tr_id: str = os.getenv("WS_TR_ID", "H0STCNT0")
    ws_tr_type: str = os.getenv("WS_TR_TYPE", "1")

    strategy_mode: str = os.getenv("STRATEGY_MODE", "momentum")
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "1.5"))
    target1_pct: float = float(os.getenv("TARGET1_PCT", "1.5"))
    target2_pct: float = float(os.getenv("TARGET2_PCT", "3.0"))

    dashboard_enabled: bool = os.getenv("DASHBOARD_ENABLED", "true").lower() == "true"
    dashboard_host: str = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    dashboard_port: int = int(os.getenv("DASHBOARD_PORT", "5055"))

settings = Settings()
settings.log_dir.mkdir(exist_ok=True)
