import requests

from ..logger import get_logger

logger = get_logger("ranking_client")


class KISRankingClient:
    """
    순위분석 API 공통 클라이언트

    - ranking_defs: [{"name","enabled","path","tr_id","params"}]
    - 응답에서 output / output1 / output2 리스트를 우선 탐색
    - 종목코드/종목명을 최대한 자동 추출
    """

    CODE_KEYS = [
        "mksc_shrn_iscd",
        "stck_shrn_iscd",
        "shrn_iscd",
        "isu_cd",
        "code",
    ]
    NAME_KEYS = [
        "hts_kor_isnm",
        "prdt_name",
        "stck_shrn_iscd_name",
        "name",
    ]

    def __init__(self, rest_client, ranking_defs: list[dict]):
        self.rest_client = rest_client
        self.ranking_defs = ranking_defs

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        token = self.rest_client.ensure_token()
        url = f"{self.rest_client.base_url}{path}"
        headers = {
            "content-type": "application/json; charset=UTF-8",
            "authorization": f"Bearer {token}",
            "appkey": self.rest_client.app_key,
            "appsecret": self.rest_client.app_secret,
            "tr_id": tr_id,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rt_cd = data.get("rt_cd")
        if rt_cd not in (None, "0"):
            raise RuntimeError(f"순위 API 호출 실패: {data}")
        return data

    def _extract_rows(self, data: dict) -> list[dict]:
        for key in ("output", "output1", "output2"):
            val = data.get(key)
            if isinstance(val, list):
                return val
        for val in data.values():
            if isinstance(val, list):
                return val
        return []

    def _extract_code_name(self, row: dict) -> tuple[str, str]:
        code = ""
        name = ""
        for k in self.CODE_KEYS:
            v = row.get(k)
            if isinstance(v, str) and len(v.strip()) == 6 and v.strip().isdigit():
                code = v.strip()
                break
        for k in self.NAME_KEYS:
            v = row.get(k)
            if isinstance(v, str) and v.strip():
                name = v.strip()
                break
        return code, (name or code)

    def fetch_candidates(self) -> tuple[dict[str, str], list[str]]:
        result: dict[str, str] = {}
        used_sources: list[str] = []

        for ranking_def in self.ranking_defs:
            if not ranking_def.get("enabled"):
                continue

            path = str(ranking_def.get("path", "")).strip()
            tr_id = str(ranking_def.get("tr_id", "")).strip()
            params = ranking_def.get("params", {}) or {}
            source_name = ranking_def.get("name", "ranking")

            if not path or not tr_id:
                logger.warning("순위소스 %s 스킵: path 또는 tr_id 비어 있음", source_name)
                continue

            try:
                data = self._get(path, tr_id, params)
                rows = self._extract_rows(data)
                if not rows:
                    logger.warning("순위소스 %s 응답에 리스트 데이터가 없습니다.", source_name)
                    continue

                before = len(result)
                for row in rows:
                    code, name = self._extract_code_name(row)
                    if code:
                        result[code] = name

                if len(result) > before:
                    used_sources.append(source_name)
                    logger.info("순위소스 %s 사용 | 신규후보=%s", source_name, len(result) - before)
            except Exception as e:
                logger.warning("순위소스 %s 호출 실패: %s", source_name, e)

        return result, used_sources
