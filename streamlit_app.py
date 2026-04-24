import os
import re
import time
import json
import zipfile
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque
from xml.etree import ElementTree as ET

import pandas as pd
import requests
import streamlit as st

# -------------------------------------------------
# 0) Streamlit secrets -> environment bridge
# -------------------------------------------------
def _load_secrets_to_env():
    try:
        for key, value in st.secrets.items():
            if isinstance(value, (str, int, float, bool)):
                os.environ[str(key)] = str(value)
    except Exception:
        pass

_load_secrets_to_env()

# -------------------------------------------------
# 1) Optional imports from current project
#    (Assumes you upload the whole kis_v8_suite repo)
# -------------------------------------------------
PROJECT_OK = True
PROJECT_IMPORT_ERROR = ""

try:
    from app.source_manager import AutoSourceManager
    from app.patterns import derive_pattern, derive_trade_levels, derive_grade
    from app.public_info import PublicInfoManager
    from app.scoring import build_snapshot, score_stock
    from app.category_parser import parse_category_file, category_for
    from app.utils import safe_int, safe_float
    from app.config import settings as project_settings
except Exception as e:
    PROJECT_OK = False
    PROJECT_IMPORT_ERROR = str(e)

# -------------------------------------------------
# 2) Basic config
# -------------------------------------------------
st.set_page_config(
    page_title="KIS Mobile Streamlit Dashboard",
    layout="wide",
    page_icon="📈"
)

APP_TITLE = os.getenv("APP_TITLE", "KIS Mobile Streamlit Dashboard")
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()
KIS_USE_MOCK = os.getenv("KIS_USE_MOCK", "false").lower() == "true"
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()
DART_API_KEY = os.getenv("DART_API_KEY", "").strip()
WATCHLIST_CODES = os.getenv("WATCHLIST_CODES", "").strip()
TOP_N_DEFAULT = int(os.getenv("TOP_N_DEFAULT", "15"))

BASE_URL = "https://openapivts.koreainvestment.com:29443" if KIS_USE_MOCK else "https://openapi.koreainvestment.com:9443"

st.title(APP_TITLE)
st.caption("무료 Streamlit Community Cloud용 모바일 대시보드. 앱이 잠들 수 있지만, 컴퓨터가 꺼져 있어도 URL로 접속 가능합니다.")

# -------------------------------------------------
# 3) Helpers
# -------------------------------------------------
TAG_RE = re.compile(r"<[^>]+>")
NORMALIZE_RE = re.compile(r"[\s\-\_\[\]\(\)\.,'\"“”‘’:/|]+")

def clean_text(text: str) -> str:
    if text is None:
        return ""
    return TAG_RE.sub("", str(text)).strip()

def normalize_text(text: str) -> str:
    return NORMALIZE_RE.sub("", clean_text(text)).lower()

def dedupe_titles(items, limit=3):
    out, seen = [], set()
    for item in items:
        nx = normalize_text(item)
        if not nx or nx in seen:
            continue
        seen.add(nx)
        out.append(clean_text(item))
        if len(out) >= limit:
            break
    return out

def fmt_num(v):
    try:
        return f"{int(float(v)):,}"
    except Exception:
        return "-"

def fmt_pct(v):
    try:
        return f"{float(v):.2f}%"
    except Exception:
        return "-"

def project_root():
    return Path(".")

def tick_cls():
    return __import__("app.models", fromlist=["TickSnapshot"]).TickSnapshot

# -------------------------------------------------
# 4) Minimal KIS REST client for Streamlit
# -------------------------------------------------
class KISMiniClient:
    def __init__(self, app_key: str, app_secret: str, use_mock: bool = False):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = "https://openapivts.koreainvestment.com:29443" if use_mock else "https://openapi.koreainvestment.com:9443"
        self._token = None
        self._token_exp = 0
        self.session = requests.Session()

    def access_token(self):
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token

        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        resp = self.session.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("access_token")
        self._token_exp = now + int(data.get("expires_in", 86400))
        return self._token

    def headers(self, tr_id: str = ""):
        token = self.access_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "custtype": "P",
        }
        if tr_id:
            headers["tr_id"] = tr_id
        return headers

    def get(self, path: str, params=None, tr_id: str = ""):
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params or {}, headers=self.headers(tr_id), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def current_price(self, code: str):
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }
        data = self.get("/uapi/domestic-stock/v1/quotations/inquire-price", params=params, tr_id="FHKST01010100")
        out = data.get("output", {}) or {}
        return {
            "code": code,
            "price": safe_int(out.get("stck_prpr")),
            "change_rate": safe_float(out.get("prdy_ctrt")),
            "volume": safe_int(out.get("acml_vol")),
            "trade_value": safe_int(out.get("acml_tr_pbmn")),
            "open_price": safe_int(out.get("stck_oprc")),
            "high_price": safe_int(out.get("stck_hgpr")),
            "low_price": safe_int(out.get("stck_lwpr")),
            "name": out.get("hts_kor_isnm") or code,
            "raw": out,
        }

    def daily_chart(self, code: str, days: int = 120):
        end = datetime.now()
        start = end - timedelta(days=max(days * 3, 180))
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
            "fid_input_date_1": start.strftime("%Y%m%d"),
            "fid_input_date_2": end.strftime("%Y%m%d"),
            "fid_period_div_code": "D",
            "fid_org_adj_prc": "0",
        }
        data = self.get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", params=params, tr_id="FHKST03010100")
        items = data.get("output2", []) or data.get("output1", []) or []
        rows = []
        for item in items:
            dt = item.get("stck_bsop_date") or item.get("date")
            close = safe_int(item.get("stck_clpr") or item.get("close"))
            open_ = safe_int(item.get("stck_oprc") or item.get("open"))
            high = safe_int(item.get("stck_hgpr") or item.get("high"))
            low = safe_int(item.get("stck_lwpr") or item.get("low"))
            vol = safe_int(item.get("acml_vol") or item.get("volume"))
            if dt and close > 0:
                rows.append({
                    "date": pd.to_datetime(dt),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": vol,
                })
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows).sort_values("date").drop_duplicates("date")
        return df.tail(days)

@st.cache_resource(show_spinner=False)
def get_kis_client():
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET 이 설정되지 않았습니다.")
    return KISMiniClient(KIS_APP_KEY, KIS_APP_SECRET, use_mock=KIS_USE_MOCK)

# -------------------------------------------------
# 5) Universe / category map
# -------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_category_map():
    if PROJECT_OK:
        try:
            return parse_category_file(project_settings.market_all_file)
        except Exception:
            return {}
    return {}

@st.cache_data(ttl=86400, show_spinner=False)
def load_watchlist_file():
    codes = []
    p = Path("watchlist.txt")
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            code = parts[0]
            if len(code) == 6 and code.isdigit():
                codes.append((code, " ".join(parts[1:]) if len(parts) > 1 else code))
    return codes

@st.cache_data(ttl=86400, show_spinner=False)
def load_universe_from_dart():
    if not DART_API_KEY:
        return {}
    try:
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        zf = zipfile.ZipFile(BytesIO(resp.content))
        xml_name = zf.namelist()[0]
        root = ET.fromstring(zf.read(xml_name))
        data = {}
        for elem in root.findall(".//list"):
            stock_code = (elem.findtext("stock_code") or "").strip()
            corp_name = (elem.findtext("corp_name") or "").strip()
            if stock_code and corp_name:
                data[stock_code] = corp_name
        return data
    except Exception:
        return {}

@st.cache_data(ttl=86400, show_spinner=False)
def build_universe():
    universe = {}
    for code, name in load_watchlist_file():
        universe[code] = name

    p = Path("market_all.txt")
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            code = parts[0]
            if len(code) == 6 and code.isdigit():
                universe[code] = " ".join(parts[1:]) if len(parts) > 1 else code

    dart_map = load_universe_from_dart()
    for code, name in dart_map.items():
        universe.setdefault(code, name)

    return universe

def resolve_query_to_code(query: str):
    q = (query or "").strip()
    if not q:
        return None, None
    universe = build_universe()

    if len(q) == 6 and q.isdigit():
        return q, universe.get(q, q)

    qn = q.lower().replace(" ", "")
    exact, partial = [], []
    for code, name in universe.items():
        nn = str(name).lower().replace(" ", "")
        if qn == nn:
            exact.append((code, name))
        elif qn in nn:
            partial.append((code, name))
    if exact:
        return exact[0]
    if partial:
        return partial[0]
    return None, None

# -------------------------------------------------
# 6) News / disclosure
# -------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def fetch_news_titles(name: str):
    if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET:
        try:
            q = requests.utils.quote(f"{name} 주식")
            url = f"https://openapi.naver.com/v1/search/news.json?query={q}&display=10&sort=date"
            headers = {
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            }
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            titles = [clean_text(x.get("title", "")) for x in data.get("items", []) if x.get("title")]
            titles = dedupe_titles(titles, limit=3)
            if titles:
                return titles
        except Exception:
            pass

    try:
        q = requests.utils.quote(f"{name} 주식")
        url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        titles = [clean_text(item.findtext("title", "")) for item in root.findall(".//item")[:10]]
        titles = dedupe_titles(titles, limit=3)
        return titles or ["최근 뉴스 없음"]
    except Exception:
        return ["최근 뉴스 없음"]

@st.cache_data(ttl=1800, show_spinner=False)
def load_dart_corp_map():
    if not DART_API_KEY:
        return {}
    try:
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        zf = zipfile.ZipFile(BytesIO(resp.content))
        xml_name = zf.namelist()[0]
        root = ET.fromstring(zf.read(xml_name))
        result = {}
        for elem in root.findall(".//list"):
            stock_code = (elem.findtext("stock_code") or "").strip()
            corp_code = (elem.findtext("corp_code") or "").strip()
            if stock_code and corp_code:
                result[stock_code] = corp_code
        return result
    except Exception:
        return {}

@st.cache_data(ttl=900, show_spinner=False)
def fetch_disclosures(stock_code: str, name: str):
    if DART_API_KEY:
        corp_map = load_dart_corp_map()
        corp_code = corp_map.get(stock_code)
        if corp_code:
            try:
                end_de = datetime.now().strftime("%Y%m%d")
                bgn_de = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
                url = "https://opendart.fss.or.kr/api/list.json"
                params = {
                    "crtfc_key": DART_API_KEY,
                    "corp_code": corp_code,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "page_no": "1",
                    "page_count": "10",
                }
                resp = requests.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                titles = [x.get("report_nm", "").strip() for x in data.get("list", []) if x.get("report_nm")]
                titles = dedupe_titles(titles, limit=3)
                if titles:
                    return titles
            except Exception:
                pass
    return ["최근 공시 없음"]

# -------------------------------------------------
# 7) Analysis engine
# -------------------------------------------------
def build_snap_for_score(cp: dict):
    return tick_cls()(
        ts=time.time(),
        price=safe_int(cp.get("price")),
        open_price=safe_int(cp.get("open_price")),
        high_price=safe_int(cp.get("high_price")),
        low_price=safe_int(cp.get("low_price")),
        volume=safe_int(cp.get("volume")),
        trade_value=safe_int(cp.get("trade_value")),
        change_rate=safe_float(cp.get("change_rate")),
    )

def build_hist_from_daily(df: pd.DataFrame):
    hist = deque(maxlen=getattr(project_settings, "history_size", 20) if PROJECT_OK else 20)
    if df is None or df.empty:
        return hist
    tail = df.tail(20)
    for _, row in tail.iterrows():
        trade_value = safe_int(row["close"]) * safe_int(row["volume"])
        hist.append(tick_cls()(
            ts=float(pd.Timestamp(row["date"]).timestamp()),
            price=safe_int(row["close"]),
            open_price=safe_int(row["open"]),
            high_price=safe_int(row["high"]),
            low_price=safe_int(row["low"]),
            volume=safe_int(row["volume"]),
            trade_value=trade_value,
            change_rate=0.0,
        ))
    return hist

def fallback_analysis(cp: dict, df: pd.DataFrame, code: str, name: str):
    category = category_for(code, load_category_map(), "관심종목") if PROJECT_OK else "관심종목"
    price = safe_int(cp.get("price"))
    high = safe_int(cp.get("high_price"))
    low = safe_int(cp.get("low_price"))
    change_rate = safe_float(cp.get("change_rate"))
    near_high = (price / high * 100.0) if high > 0 else 0.0

    vol_ratio = 1.0
    if df is not None and not df.empty and "volume" in df:
        avg_vol = float(df["volume"].tail(20).mean() or 0)
        if avg_vol > 0:
            vol_ratio = safe_int(cp.get("volume")) / avg_vol

    if change_rate >= 8 and near_high >= 98:
        pattern = "시가 돌파형"
        score = 46
    elif change_rate >= 3 and near_high >= 96:
        pattern = "상단 박스 유지형"
        score = 36
    else:
        pattern = "관찰형"
        score = 18

    grade = "A" if score >= 45 else "B" if score >= 35 else "C" if score >= 25 else "D"
    buy = int(price * 0.995) if pattern != "관찰형" else price
    stop = max(1, int(low * 0.995)) if low > 0 else int(price * 0.985)
    target1 = int(price * 1.015)
    target2 = int(price * 1.03)
    reasons = [f"등락률 {change_rate:.2f}%", f"고가근접 {near_high:.2f}%", f"거래량배수 {vol_ratio:.2f}배"]

    if score >= 45 and (price - buy) / buy * 100 <= 1.2:
        judgement = "매수 가능"
        judgement_note = "강한 추세와 고가권 유지가 확인됩니다."
    elif score >= 30:
        judgement = "눌림 대기"
        judgement_note = "관심 가능하지만 추천매수가 근처 눌림 확인이 유리합니다."
    else:
        judgement = "관망"
        judgement_note = "강도가 약합니다. 추가 확인이 필요합니다."

    return {
        "code": code,
        "name": name,
        "category": category,
        "price": price,
        "change_rate": change_rate,
        "score": score,
        "grade": grade,
        "pattern": pattern,
        "buy_price": buy,
        "stop_price": stop,
        "target1_price": target1,
        "target2_price": target2,
        "judgement": judgement,
        "judgement_note": judgement_note,
        "chart_note": "일봉 기준 단순 패턴 분석",
        "reasons": reasons,
        "near_high_pct": near_high,
        "volume": safe_int(cp.get("volume")),
        "trade_value": safe_int(cp.get("trade_value")),
        "daily_df": df,
    }

@st.cache_data(ttl=120, show_spinner=False)
def analyze_stock(code: str, name_hint: str = ""):
    client = get_kis_client()
    cp = client.current_price(code)
    name = name_hint or cp.get("name") or code
    df = client.daily_chart(code, days=120)

    if PROJECT_OK:
        try:
            hist = build_hist_from_daily(df)
            hist.append(build_snap_for_score(cp))
            score_info = score_stock(hist, project_settings)
            metrics = score_info["metrics"]
            category = category_for(code, load_category_map(), "관심종목")
            snap = build_snap_for_score(cp)
            pattern, chart_note = derive_pattern(metrics, snap)
            levels = derive_trade_levels(snap, score_info, pattern)
            grade = derive_grade(score_info["score"])

            gap = ((cp["price"] - levels["buy"]) / levels["buy"] * 100.0) if levels["buy"] else 0.0
            risk = ((levels["buy"] - levels["stop"]) / levels["buy"] * 100.0) if levels["buy"] else 0.0
            if score_info["score"] >= 45 and gap <= 1.2:
                judgement = "매수 가능"
                judgement_note = f"점수 {score_info['score']}점으로 강한 편이며 추천매수가 근처입니다. 예상 손절폭 약 {risk:.2f}%."
            elif score_info["score"] >= 35 and gap <= 3.0:
                judgement = "눌림 대기"
                judgement_note = "현재가가 추천매수가보다 다소 높아 눌림 확인 후 접근이 유리합니다."
            elif score_info["score"] >= 25:
                judgement = "관망"
                judgement_note = "패턴은 있으나 강도가 중간 수준입니다. 추가 확인이 필요합니다."
            else:
                judgement = "비추천"
                judgement_note = "점수와 패턴 강도가 낮아 보수적 접근이 유리합니다."

            result = {
                "code": code,
                "name": name,
                "category": category,
                "price": cp["price"],
                "change_rate": cp["change_rate"],
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
                "near_high_pct": metrics.get("near_high_pct", 0.0),
                "volume": cp["volume"],
                "trade_value": cp["trade_value"],
                "daily_df": df,
            }
        except Exception:
            result = fallback_analysis(cp, df, code, name)
    else:
        result = fallback_analysis(cp, df, code, name)

    result["news_titles"] = fetch_news_titles(name)
    result["disclosures"] = fetch_disclosures(code, name)
    return result

@st.cache_data(ttl=90, show_spinner=False)
def fetch_auto_candidates(top_n: int = 15):
    rows = []
    used_sources = []
    errors = []

    if PROJECT_OK:
        try:
            from app.clients.rest_client import KISRestClient as ProjectKISRestClient
            project_client = ProjectKISRestClient()
            manager = AutoSourceManager()
            ranking_codes, used_sources = manager.fetch_candidates(project_client)
            if ranking_codes:
                for code, name in list(ranking_codes.items())[: max(top_n, 20)]:
                    try:
                        rows.append(analyze_stock(code, name))
                    except Exception as e:
                        errors.append(f"{name}({code}) 분석 실패: {e}")
            else:
                errors.append("순위 소스 결과가 비어 있습니다.")
        except Exception as e:
            errors.append(f"프로젝트 순위 소스 사용 실패: {e}")
    else:
        errors.append(f"프로젝트 모듈 import 실패: {PROJECT_IMPORT_ERROR}")

    # Fallback: watchlist only
    if not rows:
        wl = load_watchlist_file()
        for code, name in wl[:top_n]:
            try:
                rows.append(analyze_stock(code, name))
            except Exception as e:
                errors.append(f"Watchlist {name}({code}) 실패: {e}")

    rows = sorted(rows, key=lambda x: (x.get("score", 0), x.get("change_rate", 0), x.get("trade_value", 0)), reverse=True)
    for idx, row in enumerate(rows, start=1):
        row["priority_rank"] = idx
    return rows[:top_n], used_sources, errors

def rows_to_df(rows):
    data = []
    for r in rows:
        data.append({
            "순위": r.get("priority_rank", 0),
            "카테고리": r.get("category", ""),
            "코드": r.get("code", ""),
            "종목명": r.get("name", ""),
            "패턴": r.get("pattern", ""),
            "등급": r.get("grade", ""),
            "현재가": r.get("price", 0),
            "등락률": r.get("change_rate", 0.0),
            "점수": r.get("score", 0),
            "추천매수가": r.get("buy_price", 0),
            "손절가": r.get("stop_price", 0),
            "1차목표가": r.get("target1_price", 0),
            "2차목표가": r.get("target2_price", 0),
            "거래량": r.get("volume", 0),
            "거래대금": r.get("trade_value", 0),
            "고가근접": round(float(r.get("near_high_pct", 0.0)), 2),
        })
    return pd.DataFrame(data)

# -------------------------------------------------
# 8) Sidebar
# -------------------------------------------------
with st.sidebar:
    st.header("설정")
    st.write(f"환경: {'모의' if KIS_USE_MOCK else '실전'}")
    st.write(f"프로젝트 모듈 연결: {'정상' if PROJECT_OK else '실패'}")
    top_n = st.slider("자동 추천 개수", min_value=5, max_value=30, value=TOP_N_DEFAULT, step=1)
    refresh_btn = st.button("자동 추천 새로고침")
    st.divider()
    st.caption("Streamlit Cloud에서는 앱이 잠들 수 있습니다. 깨우면 다시 사용할 수 있습니다.")

# -------------------------------------------------
# 9) Tabs
# -------------------------------------------------
tab_auto, tab_search, tab_watch = st.tabs(["자동 추천", "개별 종목 분석", "관심종목"])

# ---- Auto tab
with tab_auto:
    st.subheader("자동 추천")
    st.caption("현재 프로젝트의 순위 소스를 우선 사용하고, 실패하면 watchlist로 fallback 합니다.")

    if refresh_btn:
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

    with st.spinner("자동 추천 종목을 조회하는 중입니다..."):
        auto_rows, used_sources, auto_errors = fetch_auto_candidates(top_n=top_n)

    if used_sources:
        st.info("사용 소스: " + ", ".join(used_sources))
    if auto_errors:
        with st.expander("조회 경고 / 로그", expanded=False):
            for err in auto_errors:
                st.write("- " + err)

    if auto_rows:
        df = rows_to_df(auto_rows)
        categories = ["전체"] + sorted(df["카테고리"].dropna().astype(str).unique().tolist())
        selected_cat = st.selectbox("카테고리 필터", categories, index=0)
        if selected_cat != "전체":
            df = df[df["카테고리"] == selected_cat]
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("#### 상위 종목 빠른 보기")
        pick_options = {f"{r['name']} ({r['code']})": r for r in auto_rows}
        picked_label = st.selectbox("상세 보기 종목", list(pick_options.keys()))
        picked = pick_options[picked_label]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재가", fmt_num(picked["price"]))
        col2.metric("등락률", fmt_pct(picked["change_rate"]))
        col3.metric("점수 / 등급", f"{picked['score']} / {picked['grade']}")
        col4.metric("판단", picked["judgement"])

        st.write(f"**패턴**: {picked['pattern']}")
        st.write(f"**추천매수가 / 손절가 / 목표가**: {fmt_num(picked['buy_price'])} / {fmt_num(picked['stop_price'])} / {fmt_num(picked['target1_price'])}, {fmt_num(picked['target2_price'])}")
        st.write(f"**차트 사유**: {picked['chart_note']}")
        st.write(f"**점수 사유**: {', '.join(picked['reasons'])}")
        st.write("**뉴스**")
        for n in picked["news_titles"]:
            st.write("- " + n)
        st.write("**공시**")
        for d in picked["disclosures"]:
            st.write("- " + d)

        ddf = picked.get("daily_df")
        if isinstance(ddf, pd.DataFrame) and not ddf.empty:
            st.line_chart(ddf.set_index("date")[["close"]])

    else:
        st.warning("자동 추천 결과가 없습니다.")

# ---- Search tab
with tab_search:
    st.subheader("개별 종목 분석")
    st.caption("종목명 또는 6자리 종목코드를 입력하세요. DART 키가 있으면 종목명 검색이 더 잘 됩니다.")

    search_query = st.text_input("종목명 / 종목코드", placeholder="예: 삼성전자 또는 005930")
    analyze_clicked = st.button("종목 분석 실행", key="analyze_btn")

    if analyze_clicked:
        code, name = resolve_query_to_code(search_query)
        if not code:
            st.error("종목을 찾지 못했습니다. 종목코드 6자리 입력을 권장합니다.")
        else:
            with st.spinner(f"{name}({code}) 분석 중..."):
                try:
                    result = analyze_stock(code, name)
                    st.success("분석 완료")

                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("종목", f"{result['name']} ({result['code']})")
                    c2.metric("카테고리", result["category"])
                    c3.metric("패턴 / 등급", f"{result['pattern']} / {result['grade']}")
                    c4.metric("판단", result["judgement"])

                    c5, c6, c7, c8 = st.columns(4)
                    c5.metric("현재가", fmt_num(result["price"]))
                    c6.metric("등락률 / 점수", f"{fmt_pct(result['change_rate'])} / {result['score']}")
                    c7.metric("추천매수가", fmt_num(result["buy_price"]))
                    c8.metric("손절가", fmt_num(result["stop_price"]))

                    c9, c10, c11, c12 = st.columns(4)
                    c9.metric("1차 목표가", fmt_num(result["target1_price"]))
                    c10.metric("2차 목표가", fmt_num(result["target2_price"]))
                    c11.metric("고가근접", fmt_pct(result["near_high_pct"]))
                    c12.metric("거래량 / 거래대금", f"{fmt_num(result['volume'])} / {fmt_num(result['trade_value'])}")

                    st.write(f"**판단 메모**: {result['judgement_note']}")
                    st.write(f"**차트/패턴 사유**: {result['chart_note']}")
                    st.write(f"**점수 사유**: {', '.join(result['reasons'])}")

                    ddf = result.get("daily_df")
                    if isinstance(ddf, pd.DataFrame) and not ddf.empty:
                        st.markdown("#### 최근 일봉 차트")
                        st.line_chart(ddf.set_index("date")[["close"]])

                    st.markdown("#### 뉴스 분석")
                    for n in result["news_titles"]:
                        st.write("- " + n)

                    st.markdown("#### 공시 분석")
                    for d in result["disclosures"]:
                        st.write("- " + d)

                except Exception as e:
                    st.error(f"분석 중 오류가 발생했습니다: {e}")

# ---- Watchlist tab
with tab_watch:
    st.subheader("관심종목")
    st.caption("watchlist.txt 또는 WATCHLIST_CODES 환경변수를 기준으로 관심종목을 보여줍니다.")

    watch_items = load_watchlist_file()
    if WATCHLIST_CODES:
        for code in [x.strip() for x in WATCHLIST_CODES.split(",") if x.strip()]:
            if not any(code == c for c, _ in watch_items):
                watch_items.append((code, build_universe().get(code, code)))

    if not watch_items:
        st.info("watchlist.txt 또는 WATCHLIST_CODES 가 비어 있습니다.")
    else:
        if st.button("관심종목 새로고침", key="wl_refresh"):
            st.cache_data.clear()
            st.rerun()

        rows = []
        progress = st.progress(0)
        for i, (code, name) in enumerate(watch_items[:20], start=1):
            try:
                rows.append(analyze_stock(code, name))
            except Exception as e:
                st.warning(f"{name}({code}) 조회 실패: {e}")
            progress.progress(i / max(1, min(len(watch_items), 20)))

        if rows:
            wdf = rows_to_df(rows)
            st.dataframe(wdf, use_container_width=True, hide_index=True)
def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default

def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default
