import html
import json
import os
import re
import time
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

from .config import settings
from .logger import get_logger

logger = get_logger("news_disclosure")

TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(text: str) -> str:
    return TAG_RE.sub("", html.unescape(text or "")).strip()

class ExternalInfoManager:
    def __init__(self):
        self.cache: dict[str, tuple[float, str, str]] = {}
        self.refresh_sec = int(os.getenv("EXTERNAL_INFO_REFRESH_SEC", "900"))
        self.enable_news = os.getenv("ENABLE_NEWS_FETCH", "true").lower() == "true"
        self.enable_disclosure = os.getenv("ENABLE_DISCLOSURE_FETCH", "true").lower() == "true"
        self.naver_client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
        self.naver_client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
        self.dart_api_key = os.getenv("DART_API_KEY", "").strip()
        self.dart_cache_file = settings.base_dir / "dart_corp_cache.json"

    def get(self, code: str, name: str) -> tuple[str, str]:
        now = time.time()
        cached = self.cache.get(code)
        if cached and (now - cached[0]) < self.refresh_sec:
            return cached[1], cached[2]

        news = self.fetch_news(name) if self.enable_news else ""
        disclosure = self.fetch_disclosure(code) if self.enable_disclosure else ""
        self.cache[code] = (now, news, disclosure)
        return news, disclosure

    def fetch_news(self, name: str) -> str:
        if not (self.naver_client_id and self.naver_client_secret):
            return "뉴스 API 미설정"
        try:
            query = quote(f"{name} 주식")
            url = f"https://openapi.naver.com/v1/search/news.json?query={query}&display=3&sort=date"
            headers = {
                "X-Naver-Client-Id": self.naver_client_id,
                "X-Naver-Client-Secret": self.naver_client_secret,
            }
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            titles = [_strip_html(x.get("title", "")) for x in items[:3] if x.get("title")]
            if not titles:
                return "최근 뉴스 없음"
            return " | ".join(titles)
        except Exception as e:
            logger.warning("뉴스 조회 실패 - %s: %s", name, e)
            return "뉴스 조회 실패"

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
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            zf = zipfile.ZipFile(BytesIO(resp.content))
            name = zf.namelist()[0]
            xml_bytes = zf.read(name)
            root = ET.fromstring(xml_bytes)
            result = {}
            for elem in root.findall(".//list"):
                stock_code = (elem.findtext("stock_code") or "").strip()
                corp_code = (elem.findtext("corp_code") or "").strip()
                if stock_code and corp_code:
                    result[stock_code] = corp_code
            self.dart_cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return result
        except Exception as e:
            logger.warning("DART 고유번호 캐시 생성 실패: %s", e)
            return {}

    def fetch_disclosure(self, stock_code: str) -> str:
        if not self.dart_api_key:
            return "DART API 미설정"

        corp_map = self._load_dart_corp_map()
        corp_code = corp_map.get(stock_code)
        if not corp_code:
            return "기업 고유번호 없음"

        try:
            now = time.strftime("%Y%m%d")
            # 최근 7일
            from datetime import datetime, timedelta
            bgn = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
            url = "https://opendart.fss.or.kr/api/list.json"
            params = {
                "crtfc_key": self.dart_api_key,
                "corp_code": corp_code,
                "bgn_de": bgn,
                "end_de": now,
                "page_no": "1",
                "page_count": "3",
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("list", [])
            titles = [x.get("report_nm", "").strip() for x in items[:3] if x.get("report_nm")]
            if not titles:
                return "최근 공시 없음"
            return " | ".join(titles)
        except Exception as e:
            logger.warning("공시 조회 실패 - %s: %s", stock_code, e)
            return "공시 조회 실패"
