import logging
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .category_parser import category_for, parse_category_file
from .clients.rest_client import KISRestClient
from .config import settings
from .logger import get_logger
from .patterns import derive_grade, derive_pattern, derive_trade_levels
from .public_info import PublicInfoManager
from .scoring import build_snapshot, score_stock
from .state import store
from .utils import safe_float, safe_int

logger = get_logger("dashboard_v12_6_2")
app = Flask(__name__, template_folder="templates")

_public_info = PublicInfoManager()
_category_map = parse_category_file(settings.market_all_file)
_client = None
_client_error = None

def _tick_cls():
    return __import__("app.models", fromlist=["TickSnapshot"]).TickSnapshot

def _get_summary():
    if hasattr(store, "get_summary") and callable(store.get_summary):
        return store.get_summary()
    return getattr(store, "summary", "준비")

def _get_candidates():
    if hasattr(store, "get_candidates") and callable(store.get_candidates):
        return store.get_candidates()
    return getattr(store, "candidates", [])

def _load_universe():
    items = {}
    for path in [settings.watchlist_file, settings.market_all_file]:
        p = Path(path)
        if not p.exists():
            continue
        current_category = "관심종목"
        for raw in p.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                title = line.lstrip("#").strip().strip("=").strip()
                if title:
                    current_category = title
                continue
            parts = line.split()
            code = parts[0]
            name = " ".join(parts[1:]) if len(parts) > 1 else code
            if len(code) == 6 and code.isdigit():
                items[code] = {"name": name, "category": current_category}
    return items

_UNIVERSE = _load_universe()

def _resolve_query(query: str):
    q = (query or "").strip()
    if not q:
        return None, None, None

    if len(q) == 6 and q.isdigit():
        info = _UNIVERSE.get(q)
        if info:
            return q, info["name"], info["category"]
        return q, q, category_for(q, _category_map)

    q_norm = q.lower().replace(" ", "")
    exact = []
    partial = []
    for code, info in _UNIVERSE.items():
        name = info["name"]
        name_norm = name.lower().replace(" ", "")
        if q_norm == name_norm:
            exact.append((code, name, info["category"]))
        elif q_norm in name_norm:
            partial.append((code, name, info["category"]))

    if exact:
        return exact[0]
    if partial:
        return partial[0]
    return None, None, None

def _find_row_in_store(code: str, name: str):
    for row in _get_candidates():
        if getattr(row, "code", "") == code or getattr(row, "name", "") == name:
            return row
    return None

def _row_to_analysis(row):
    score = getattr(row, "score", 0)
    buy = getattr(row, "buy_price", 0)
    stop = getattr(row, "stop_price", 0)
    price = getattr(row, "price", 0)
    gap = ((price - buy) / buy * 100.0) if buy else 0.0
    risk = ((buy - stop) / buy * 100.0) if buy else 0.0

    if score >= 45 and gap <= 1.2:
        judgement, note = "매수 가능", f"점수 {score}점으로 강한 편이며 추천매수가 근처입니다. 예상 손절폭 약 {risk:.2f}%."
    elif score >= 35 and gap <= 3.0:
        judgement, note = "눌림 대기", "현재가가 추천매수가보다 다소 높아 눌림 확인 후 접근이 유리합니다."
    elif score >= 25:
        judgement, note = "관망", "패턴은 있으나 강도가 중간 수준입니다. 추가 확인이 필요합니다."
    else:
        judgement, note = "비추천", "점수와 패턴 강도가 낮아 보수적 접근이 유리합니다."

    return {
        "ok": True,
        "code": getattr(row, "code", ""),
        "name": getattr(row, "name", ""),
        "category": getattr(row, "category", ""),
        "price": getattr(row, "price", 0),
        "change_rate": getattr(row, "change_rate", 0.0),
        "score": score,
        "grade": getattr(row, "signal_grade", ""),
        "pattern": getattr(row, "pattern", ""),
        "buy_price": getattr(row, "buy_price", 0),
        "stop_price": getattr(row, "stop_price", 0),
        "target1_price": getattr(row, "target1_price", 0),
        "target2_price": getattr(row, "target2_price", 0),
        "judgement": judgement,
        "judgement_note": note,
        "chart_note": getattr(row, "chart_note", ""),
        "reasons": getattr(row, "reasons", []),
        "news_summary": getattr(row, "news_summary", "최근 뉴스 없음"),
        "disclosure_summary": getattr(row, "disclosure_summary", "최근 공시 없음"),
        "near_high_pct": getattr(row, "near_high_pct", 0.0),
        "volume": getattr(row, "volume", 0),
        "trade_value": getattr(row, "trade_value", 0),
    }

def _get_client():
    global _client, _client_error
    if _client is not None:
        return _client
    try:
        _client = KISRestClient()
        _client_error = None
        return _client
    except Exception as e:
        _client_error = str(e)
        raise

def _buy_judgement(score: int, price: int, buy: int, stop: int):
    gap = ((price - buy) / buy * 100.0) if buy > 0 else 0.0
    risk = ((buy - stop) / buy * 100.0) if buy > 0 else 0.0

    if score >= 45 and gap <= 1.2:
        return "매수 가능", f"점수 {score}점으로 강한 편이며 추천매수가 근처입니다. 예상 손절폭 약 {risk:.2f}%."
    if score >= 35 and gap <= 3.0:
        return "눌림 대기", f"관심은 가능하지만 현재가가 추천매수가보다 약간 높습니다. 눌림 확인 후 접근이 유리합니다."
    if score >= 25:
        return "관망", "패턴은 있으나 확정 신호 강도가 부족합니다. 추가 거래량/고가권 유지 확인이 필요합니다."
    return "비추천", "점수와 패턴 강도가 낮아 지금 즉시 매수 판단은 보수적으로 보는 편이 좋습니다."

def _analyze_query(query: str):
    code, name, category = _resolve_query(query)
    if not code:
        return {"ok": False, "message": "종목을 찾지 못했습니다. 코드 6자리 또는 종목명을 입력해 주세요."}

    # 1) 자동 추천 목록에 이미 있으면 저장된 분석값을 우선 사용
    cached_row = _find_row_in_store(code, name)
    if cached_row is not None:
        return _row_to_analysis(cached_row)

    # 2) 없으면 KIS API로 직접 분석
    try:
        client = _get_client()
    except Exception as e:
        return {
            "ok": False,
            "message": f"KIS 인증 오류로 개별 종목 분석에 실패했습니다. 현재 자동추천 목록에 없는 종목은 분석할 수 없습니다. 상세: {e}"
        }

    output = client.inquire_price(code)
    snap = build_snapshot(output, safe_int, safe_float, _tick_cls())

    if snap.price <= 0:
        return {"ok": False, "message": "현재가를 가져오지 못했습니다."}

    try:
        if hasattr(store, "ensure_state") and callable(store.ensure_state):
            state = store.ensure_state(code, name)
            state.history.append(snap)
            hist = state.history
        else:
            hist = deque([snap], maxlen=getattr(settings, "history_size", 20))
    except Exception:
        hist = deque([snap], maxlen=getattr(settings, "history_size", 20))

    score_info = score_stock(hist, settings)
    metrics = score_info["metrics"]
    pattern, chart_note = derive_pattern(metrics, snap)
    levels = derive_trade_levels(snap, score_info, pattern)
    grade = derive_grade(score_info["score"])

    try:
        news_summary, disclosure_summary = _public_info.get(code, name)
    except Exception as e:
        logger.warning("외부 정보 조회 실패 - %s(%s): %s", name, code, e)
        news_summary, disclosure_summary = "최근 뉴스 없음", "최근 공시 없음"

    judgement, judgement_note = _buy_judgement(score_info["score"], snap.price, levels["buy"], levels["stop"])

    return {
        "ok": True,
        "code": code,
        "name": name,
        "category": category,
        "price": snap.price,
        "change_rate": snap.change_rate,
        "score": score_info["score"],
        "grade": grade,
        "pattern": pattern,
        "buy_price": levels["buy"],
        "stop_price": levels["stop"],
        "target1_price": levels["target1"],
        "target2_price": levels["target2"],
        "judgement": judgement,
        "judgement_note": judgement_note,
        "chart_note": chart_note,
        "reasons": score_info["reasons"],
        "news_summary": news_summary,
        "disclosure_summary": disclosure_summary,
        "near_high_pct": metrics.get("near_high_pct", 0.0),
        "volume": snap.volume,
        "trade_value": snap.trade_value,
    }

@app.route("/")
def index():
    return render_template("index.html", rows=_get_candidates(), summary=_get_summary())

@app.route("/api/analyze", methods=["GET"])
def api_analyze():
    try:
        query = request.args.get("query", "").strip()
        result = _analyze_query(query)
        return jsonify(result), 200
    except Exception as e:
        logger.exception("개별 종목 분석 실패: %s", e)
        return jsonify({
            "ok": False,
            "message": f"개별 종목 분석 중 오류가 발생했습니다: {e}"
        }), 200

def run_dashboard():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("flask.app").setLevel(logging.ERROR)
    logger.info("대시보드 시작: http://127.0.0.1:%s", settings.dashboard_port)
    app.run(host="127.0.0.1", port=settings.dashboard_port, debug=False, use_reloader=False)
