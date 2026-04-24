import html
import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

from .config import settings
from .logger import get_logger

logger = get_logger("public_info")

TAG_RE = re.compile(r"<[^>]+>")
NORMALIZE_RE = re.compile(r"[\s\-\_\[\]\(\)\.,'\"“”‘’:/|]+")

def _clean(text: str) -> str:
    return TAG_RE.sub("", html.unescape(text or "")).strip()

def _norm(text: str) -> str:
    return NORMALIZE_RE.sub("", _clean(text)).lower()

def _dedupe_titles(items: list[str], limit: int = 3) -> list[str]:
    out = []
    seen = set()
    for x in items:
        nx = _norm(x)
        if not nx or nx in seen:
            continue
        seen.add(nx)
        out.append(_clean(x))
        if len(out) >= limit:
            break
    return out

class PublicInfoManager:
    def __init__(self):
        self.cache = {}
        self.refresh_sec = int(os.getenv("EXTERNAL_INFO_REFRESH_SEC", "900"))
        self.enable_news = os.getenv("ENABLE_NEWS_FETCH", "true").lower() == "true"
        self.enable_disclosure = os.getenv("ENABLE_DISCLOSURE_FETCH", "true").lower() == "true"
        self.naver_client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
        self.naver_client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
        self.dart_api_key = os.getenv("DART_API_KEY", "").strip()
        self.dart_cache_file = settings.base_dir / "dart_corp_cache.json"
        self.fast_timeout = float(os.getenv("PUBLIC_INFO_TIMEOUT_SEC", "3"))
        self.max_news_items = int(os.getenv("PUBLIC_INFO_NEWS_LIMIT", "3"))
        self.max_disclosure_items = int(os.getenv("PUBLIC_INFO_DISC_LIMIT", "3"))

    def get(self, code: str, name: str) -> tuple[str, str]:
        now = time.time()
        cached = self.cache.get(code)
        if cached and (now - cached[0]) < self.refresh_sec:
            return cached[1], cached[2]

        try:
            news = self.fetch_news(name) if self.enable_news else "뉴스 비활성화"
        except Exception as e:
            logger.warning("뉴스 처리 실패 - %s: %s", name, e)
            news = "최근 뉴스 없음"

        try:
            disclosure = self.fetch_disclosure(code, name) if self.enable_disclosure else "공시 비활성화"
        except Exception as e:
            logger.warning("공시 처리 실패 - %s(%s): %s", name, code, e)
            disclosure = "최근 공시 없음"

        self.cache[code] = (now, news, disclosure)
        return news, disclosure

    def fetch_news(self, name: str) -> str:
        if self.naver_client_id and self.naver_client_secret:
            try:
                q = quote(f"{name} 주식")
                url = f"https://openapi.naver.com/v1/search/news.json?query={q}&display=10&sort=date"
                headers = {
                    "X-Naver-Client-Id": self.naver_client_id,
                    "X-Naver-Client-Secret": self.naver_client_secret,
                }
                resp = requests.get(url, headers=headers, timeout=self.fast_timeout)
                resp.raise_for_status()
                data = resp.json()

                name_norm = _norm(name)
                priority, others = [], []
                for item in data.get("items", []):
                    title = _clean(item.get("title", ""))
                    if not title:
                        continue
                    if name_norm and name_norm in _norm(title):
                        priority.append(title)
                    else:
                        others.append(title)

                titles = _dedupe_titles(priority + others, limit=self.max_news_items)
                if titles:
                    return " | ".join(titles)
            except Exception as e:
                logger.warning("네이버 뉴스 조회 실패 - %s: %s", name, e)

        try:
            q = quote(f"{name} 주식")
            url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
            resp = requests.get(url, timeout=self.fast_timeout, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            items = root.findall(".//item")

            name_norm = _norm(name)
            priority, others = [], []
            for item in items[:10]:
                title = _clean(item.findtext("title", ""))
                if not title:
                    continue
                if name_norm and name_norm in _norm(title):
                    priority.append(title)
                else:
                    others.append(title)

            titles = _dedupe_titles(priority + others, limit=self.max_news_items)
            if titles:
                return " | ".join(titles)
            return "최근 뉴스 없음"
        except Exception as e:
            logger.warning("공개 뉴스 조회 실패 - %s: %s", name, e)
            return "최근 뉴스 없음"

    def _load_dart_corp_map(self) -> dict[str, str]:
        if self.dart_cache_file.exists():
            try:
                data = json.loads(self.dart_cache_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    return data
            except Exception:
                pass

        if not self.dart_api_key:
            return {}

        try:
            url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={self.dart_api_key}"
            resp = requests.get(url, timeout=self.fast_timeout + 2)
            resp.raise_for_status()

            zf = zipfile.ZipFile(BytesIO(resp.content))
            xml_name = zf.namelist()[0]
            xml_bytes = zf.read(xml_name)
            root = ET.fromstring(xml_bytes)

            result = {}
            for elem in root.findall(".//list"):
                stock_code = (elem.findtext("stock_code") or "").strip()
                corp_code = (elem.findtext("corp_code") or "").strip()
                if stock_code and corp_code:
                    result[stock_code] = corp_code

            if result:
                self.dart_cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return result
        except Exception as e:
            logger.warning("DART 고유번호 캐시 생성 실패: %s", e)
            return {}

    def fetch_disclosure(self, stock_code: str, name: str) -> str:
        if self.dart_api_key:
            try:
                corp_map = self._load_dart_corp_map()
                corp_code = corp_map.get(stock_code)
                if corp_code:
                    end_de = datetime.now().strftime("%Y%m%d")
                    bgn_de = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
                    url = "https://opendart.fss.or.kr/api/list.json"
                    params = {
                        "crtfc_key": self.dart_api_key,
                        "corp_code": corp_code,
                        "bgn_de": bgn_de,
                        "end_de": end_de,
                        "page_no": "1",
                        "page_count": "10",
                    }
                    resp = requests.get(url, params=params, timeout=self.fast_timeout)
                    resp.raise_for_status()
                    data = resp.json()
                    titles = _dedupe_titles(
                        [x.get("report_nm", "").strip() for x in data.get("list", []) if x.get("report_nm")],
                        limit=self.max_disclosure_items,
                    )
                    if titles:
                        return " | ".join(titles)
            except Exception as e:
                logger.warning("DART 공시 조회 실패 - %s(%s): %s", name, stock_code, e)

        try:
            q = quote(name)
            urls = [
                f"https://kind.krx.co.kr/disclosure/todaydisclosure.do?method=searchTodayDisclosureMain&searchType=13&marketType=all&repIsuSrtCd=&kosdaqSegment=&stockSegment=&keyword={q}",
                f"https://kind.krx.co.kr/common/rss/RSS4TodayDisList.do?method=searchTodayDisList&keyword={q}",
            ]
            candidates = []
            for url in urls:
                try:
                    resp = requests.get(url, timeout=self.fast_timeout, headers={"User-Agent": "Mozilla/5.0"})
                    if not resp.ok:
                        continue
                    for line in resp.text.splitlines():
                        line = _clean(line)
                        if not line:
                            continue
                        if name in line and len(line) <= 120:
                            candidates.append(line)
                except Exception:
                    pass

            titles = _dedupe_titles(candidates, limit=self.max_disclosure_items)
            if titles:
                return " | ".join(titles)
            return "최근 공시 없음"
        except Exception as e:
            logger.warning("KIND 공시 조회 실패 - %s(%s): %s", name, stock_code, e)
            return "최근 공시 없음"
