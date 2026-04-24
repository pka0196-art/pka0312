import time
from collections import deque
from .utils import mean

def build_snapshot(output, safe_int, safe_float, TickSnapshot):
    return TickSnapshot(
        ts=time.time(),
        price=safe_int(output.get("stck_prpr") or output.get("last")),
        open_price=safe_int(output.get("stck_oprc")),
        high_price=safe_int(output.get("stck_hgpr")),
        low_price=safe_int(output.get("stck_lwpr")),
        volume=safe_int(output.get("acml_vol")),
        trade_value=safe_int(output.get("acml_tr_pbmn") or output.get("acml_tr_pbmn1") or output.get("acml_tr_pbmn2")),
        change_rate=safe_float(output.get("prdy_ctrt")),
    )

def get_volume_delta(cur, prev) -> int:
    return max(0, cur.volume - prev.volume)

def get_trade_value_delta(cur, prev) -> int:
    return max(0, cur.trade_value - prev.trade_value)

def derive_trend_metrics(history: deque) -> dict:
    items = list(history)
    if len(items) < 2:
        return {
            "price_step_pct": 0.0,
            "vol_delta": 0,
            "trade_delta": 0,
            "avg_vol_delta": 0.0,
            "avg_trade_delta": 0.0,
            "volume_surge_ratio": 0.0,
            "trade_surge_ratio": 0.0,
            "near_high_pct": 0.0,
            "range_position_pct": 0.0,
        }

    cur = items[-1]
    prev = items[-2]
    price_step_pct = ((cur.price - prev.price) / prev.price * 100.0) if prev.price > 0 else 0.0
    vol_delta = get_volume_delta(cur, prev)
    trade_delta = get_trade_value_delta(cur, prev)

    vol_deltas, trade_deltas = [], []
    for i in range(1, len(items) - 1):
        older = items[i - 1]
        newer = items[i]
        vol_deltas.append(get_volume_delta(newer, older))
        trade_deltas.append(get_trade_value_delta(newer, older))

    avg_vol_delta = mean([x for x in vol_deltas if x > 0]) or 0.0
    avg_trade_delta = mean([x for x in trade_deltas if x > 0]) or 0.0

    volume_surge_ratio = (vol_delta / avg_vol_delta) if avg_vol_delta > 0 else (2.0 if vol_delta > 0 else 0.0)
    trade_surge_ratio = (trade_delta / avg_trade_delta) if avg_trade_delta > 0 else (2.0 if trade_delta > 0 else 0.0)
    near_high_pct = (cur.price / cur.high_price * 100.0) if cur.high_price > 0 else 0.0
    range_position_pct = ((cur.price - cur.low_price) / (cur.high_price - cur.low_price) * 100.0) if cur.high_price > cur.low_price else 0.0

    return {
        "price_step_pct": price_step_pct,
        "vol_delta": vol_delta,
        "trade_delta": trade_delta,
        "avg_vol_delta": avg_vol_delta,
        "avg_trade_delta": avg_trade_delta,
        "volume_surge_ratio": volume_surge_ratio,
        "trade_surge_ratio": trade_surge_ratio,
        "near_high_pct": near_high_pct,
        "range_position_pct": range_position_pct,
    }

def calc_trade_plan(cur, settings) -> dict:
    entry = cur.price
    stop = int(cur.price * (1 - settings.stop_loss_pct / 100))
    target1 = int(cur.price * (1 + settings.target1_pct / 100))
    target2 = int(cur.price * (1 + settings.target2_pct / 100))
    return {"entry": entry, "stop": stop, "target1": target1, "target2": target2}

def score_stock(history: deque, settings) -> dict:
    if len(history) < 2:
        return {"score": 0, "reasons": ["데이터 수집중"], "signal_ready": False, "metrics": derive_trend_metrics(history), "trade_plan": None}

    cur = history[-1]
    metrics = derive_trend_metrics(history)
    score = 0
    reasons = []

    if cur.change_rate >= 0.3:
        score += 8; reasons.append(f"등락률 +{cur.change_rate:.2f}%")
    if cur.change_rate >= 1.0:
        score += 10; reasons.append("상승 추세 강화")
    if cur.change_rate >= 2.0:
        score += 10; reasons.append("강한 상승률")

    if metrics["price_step_pct"] >= 0.15:
        score += 8; reasons.append(f"직전 샘플 대비 +{metrics['price_step_pct']:.2f}%")
    if metrics["price_step_pct"] >= 0.30:
        score += 10; reasons.append("가격 가속")

    if metrics["volume_surge_ratio"] >= 1.4:
        score += 10; reasons.append(f"거래량 가속 x{metrics['volume_surge_ratio']:.2f}")
    if metrics["volume_surge_ratio"] >= 2.0:
        score += 10; reasons.append("거래량 급증")

    if metrics["trade_surge_ratio"] >= 1.3:
        score += 10; reasons.append(f"거래대금 가속 x{metrics['trade_surge_ratio']:.2f}")
    if metrics["trade_surge_ratio"] >= 2.0:
        score += 10; reasons.append("거래대금 강한 유입")

    if cur.open_price > 0 and cur.price > cur.open_price:
        score += 8; reasons.append("시가 상회")
    if metrics["near_high_pct"] >= 99.0:
        score += 12; reasons.append("당일 고가 근접")
    elif metrics["near_high_pct"] >= 97.8:
        score += 7; reasons.append("고가권 유지")

    if metrics["range_position_pct"] >= 80:
        score += 8; reasons.append("당일 상단 박스권")

    if cur.change_rate < 0:
        score -= 15
    if cur.open_price > 0 and cur.price <= cur.open_price:
        score -= 10
    if metrics["price_step_pct"] < 0:
        score -= 8

    score = max(0, min(score, 100))
    signal_ready = (
        score >= settings.alert_score_threshold
        and cur.change_rate >= settings.min_change_rate_for_alert
        and metrics["near_high_pct"] >= settings.min_near_high_pct_for_alert
        and metrics["volume_surge_ratio"] >= settings.min_volume_surge_ratio_for_alert
        and metrics["trade_surge_ratio"] >= settings.min_trade_surge_ratio_for_alert
        and cur.price > cur.open_price > 0
    )
    return {
        "score": score,
        "reasons": reasons or ["특이점 없음"],
        "signal_ready": signal_ready,
        "metrics": metrics,
        "trade_plan": calc_trade_plan(cur, settings),
    }

def market_prefilter_score(snap, history: deque, settings) -> float:
    metrics = derive_trend_metrics(history)
    score = 0.0
    score += max(-2.0, min(6.0, snap.change_rate))
    score += min(5.0, metrics["price_step_pct"] * 8.0)
    score += min(5.0, metrics["volume_surge_ratio"] * 1.8)
    score += min(5.0, metrics["trade_surge_ratio"] * 1.5)
    score += min(3.0, max(0.0, (metrics["near_high_pct"] - 95.0) / 1.5))
    if snap.open_price > 0 and snap.price > snap.open_price:
        score += 2.0
    if snap.price < settings.min_price_filter or snap.price > settings.max_price_filter:
        score -= 100.0
    if snap.change_rate < settings.min_base_change_filter:
        score -= 100.0
    return score