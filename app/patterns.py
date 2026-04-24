def _day_range_pct(snap) -> float:
    if snap.low_price <= 0:
        return 0.0
    return ((snap.high_price - snap.low_price) / snap.low_price) * 100.0

def _body_pct(snap) -> float:
    if snap.open_price <= 0:
        return 0.0
    return ((snap.price - snap.open_price) / snap.open_price) * 100.0

def derive_pattern(metrics: dict, snap) -> tuple[str, str]:
    near_high = metrics.get("near_high_pct", 0.0)
    v_ratio = metrics.get("volume_surge_ratio", 0.0)
    t_ratio = metrics.get("trade_surge_ratio", 0.0)
    step = metrics.get("price_step_pct", 0.0)
    range_pos = metrics.get("range_position_pct", 0.0)
    body = _body_pct(snap)
    day_range = _day_range_pct(snap)

    if snap.change_rate >= 12 and near_high >= 99 and t_ratio >= 1.6:
        return "초강세 급등 추세형", "급등권에서 거래대금이 강하게 붙고 고가권을 유지"
    if body >= 3 and snap.price > snap.open_price and near_high >= 98:
        return "시가 돌파형", "시가 위에서 강한 몸통과 함께 고가권 유지"
    if snap.change_rate >= 5 and near_high >= 97 and 1.2 <= day_range <= 8 and range_pos >= 80:
        return "돌파 지속형", "돌파 후 상단에서 눌림 없이 버티는 구조"
    if v_ratio >= 2.0 and step > 0 and range_pos >= 70:
        return "거래량 급증형", "직전 구간 대비 거래량 증가가 빠르고 상단 유지"
    if snap.change_rate >= 2 and near_high >= 96 and v_ratio < 1.2:
        return "상단 박스 유지형", "강한 거래량은 아니지만 고가권 유지"
    if -1.0 <= snap.change_rate <= 3.0 and range_pos >= 45:
        return "눌림 반등 대기형", "추세 재개 여부를 확인할 관찰 구간"
    return "관찰형", "추가 데이터 확인 필요"

def derive_trade_levels(snap, score_info: dict, pattern: str = "") -> dict:
    price = int(snap.price)
    low = int(snap.low_price) if snap.low_price > 0 else price
    high = int(snap.high_price) if snap.high_price > 0 else price
    plan = score_info.get("trade_plan") or {}

    entry = int(plan.get("entry", 0) or price)
    stop = int(plan.get("stop", 0) or max(1, int(price * 0.985)))
    target1 = int(plan.get("target1", 0) or int(price * 1.015))
    target2 = int(plan.get("target2", 0) or int(price * 1.03))

    if pattern == "초강세 급등 추세형":
        buy = max(1, int(price * 0.992))
        stop = max(1, int(low * 0.998))
        target1 = max(target1, int(price * 1.020))
        target2 = max(target2, int(price * 1.045))
    elif pattern == "시가 돌파형":
        buy = max(1, int(price * 0.995))
        stop = max(1, int(min(low, snap.open_price) * 0.998))
        target1 = max(target1, int(price * 1.015))
        target2 = max(target2, int(price * 1.030))
    elif pattern == "돌파 지속형":
        buy = max(1, int(price * 0.996))
        stop = max(1, int(low * 0.997))
        target1 = max(target1, int(price * 1.012))
        target2 = max(target2, int(price * 1.025))
    elif pattern == "거래량 급증형":
        buy = max(1, int(price * 0.997))
        stop = max(1, int(low * 0.996))
        target1 = max(target1, int(price * 1.012))
        target2 = max(target2, int(price * 1.022))
    elif pattern == "상단 박스 유지형":
        box_mid = int((high + low) / 2) if high > 0 and low > 0 else price
        buy = min(price, max(1, int(box_mid)))
        stop = max(1, int(low * 0.995))
        target1 = max(target1, int(high * 1.005))
        target2 = max(target2, int(high * 1.015))
    elif pattern == "눌림 반등 대기형":
        buy = max(1, int(price * 0.994))
        stop = max(1, int(low * 0.993))
        target1 = max(target1, int(price * 1.010))
        target2 = max(target2, int(price * 1.020))
    else:
        buy = entry

    return {
        "buy": buy,
        "stop": stop,
        "target1": target1,
        "target2": target2,
    }

def derive_grade(score: int) -> str:
    if score >= 55:
        return "A"
    if score >= 40:
        return "B"
    if score >= 25:
        return "C"
    return "D"
