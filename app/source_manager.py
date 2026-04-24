import json
import os
import time

from .config import settings
from .logger import get_logger
from .clients.ranking_client import KISRankingClient

logger = get_logger("source_manager")


class AutoSourceManager:
    """
    V9.2 자동 추천 소스 관리자

    추가 개선
    - ranking_sources.json 이 없으면 자동 생성
    - 파일이 비어 있거나 JSON 오류면 자동 복구
    - .env 에 있는 소스 템플릿을 파일에 자동 병합
    - 성공한 소스를 api_source_cache.json 에 저장하고 다음 실행 때 우선 사용
    - 전부 실패하면 runner 쪽에서 market_all.txt fallback
    """

    def __init__(self):
        self.sources_file = settings.base_dir / os.getenv("RANKING_SOURCES_FILE", "ranking_sources.json")
        self.cache_file = settings.base_dir / "api_source_cache.json"
        self.refresh_minutes = int(os.getenv("SOURCE_MANAGER_REFRESH_MINUTES", "60"))
        self.require_rows = os.getenv("SOURCE_MANAGER_REQUIRE_ROWS", "true").lower() == "true"
        self.auto_create = os.getenv("AUTO_CREATE_RANKING_SOURCES", "true").lower() == "true"
        self.auto_merge_env_sources = os.getenv("AUTO_MERGE_ENV_SOURCES", "true").lower() == "true"
        self._ensure_sources_file()

    def _json_env(self, name: str, default: str = "{}") -> dict:
        raw = os.getenv(name, default)
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            logger.warning("%s JSON 파싱 실패", name)
            return {}

    def _default_sources_template(self) -> list[dict]:
        return [
            {
                "name": "volume_rank",
                "enabled": os.getenv("RANKING_VOLUME_ENABLED", "true").lower() == "true",
                "path": os.getenv("RANKING_VOLUME_PATH", "/uapi/domestic-stock/v1/quotations/volume-rank"),
                "tr_id": os.getenv("RANKING_VOLUME_TR_ID", "").strip(),
                "params": self._json_env("RANKING_VOLUME_PARAMS_JSON"),
            },
            {
                "name": "expected_updown",
                "enabled": os.getenv("RANKING_EXPECTED_ENABLED", "true").lower() == "true",
                "path": os.getenv("RANKING_EXPECTED_PATH", "/uapi/domestic-stock/v1/ranking/exp-trans-updown"),
                "tr_id": os.getenv("RANKING_EXPECTED_TR_ID", "").strip(),
                "params": self._json_env("RANKING_EXPECTED_PARAMS_JSON"),
            },
            {
                "name": "trade_power",
                "enabled": os.getenv("RANKING_TRADE_POWER_ENABLED", "false").lower() == "true",
                "path": os.getenv("RANKING_TRADE_POWER_PATH", "").strip(),
                "tr_id": os.getenv("RANKING_TRADE_POWER_TR_ID", "").strip(),
                "params": self._json_env("RANKING_TRADE_POWER_PARAMS_JSON"),
            },
        ]

    def _normalize(self, source: dict) -> dict:
        return {
            "name": str(source.get("name", "")).strip(),
            "enabled": bool(source.get("enabled", True)),
            "path": str(source.get("path", "")).strip(),
            "tr_id": str(source.get("tr_id", "")).strip(),
            "params": source.get("params", {}) if isinstance(source.get("params", {}), dict) else {},
        }

    def _save_sources(self, sources: list[dict]) -> None:
        self.sources_file.write_text(
            json.dumps(sources, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_sources_file(self) -> list[dict]:
        if not self.sources_file.exists():
            return []
        try:
            data = json.loads(self.sources_file.read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                return [self._normalize(x) for x in data if isinstance(x, dict)]
        except Exception as e:
            logger.warning("ranking_sources.json 읽기 실패: %s", e)
        return []

    def _ensure_sources_file(self) -> None:
        template_sources = [self._normalize(x) for x in self._default_sources_template() if self._normalize(x)["name"]]

        if not self.sources_file.exists():
            if self.auto_create:
                self._save_sources(template_sources)
                logger.info("ranking_sources.json 자동 생성 완료: %s", self.sources_file.name)
            return

        file_sources = self._read_sources_file()

        if not file_sources:
            if self.auto_create:
                backup = self.sources_file.with_suffix(".broken.bak")
                try:
                    if self.sources_file.exists():
                        backup.write_text(self.sources_file.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
                except Exception:
                    pass
                self._save_sources(template_sources)
                logger.info("ranking_sources.json 자동 복구 완료")
            return

        if self.auto_merge_env_sources:
            by_name = {s["name"]: s for s in file_sources if s["name"]}
            changed = False
            for src in template_sources:
                if src["name"] not in by_name:
                    by_name[src["name"]] = src
                    changed = True
            if changed:
                merged = list(by_name.values())
                self._save_sources(merged)
                logger.info("ranking_sources.json 에 .env 소스 자동 병합 완료")

    def _load_cache(self) -> dict:
        if not self.cache_file.exists():
            return {}
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
        return {}

    def _save_cache(self, data: dict) -> None:
        self.cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cache_is_fresh(self, cache: dict) -> bool:
        ts = float(cache.get("ts", 0) or 0)
        if ts <= 0:
            return False
        return (time.time() - ts) < (self.refresh_minutes * 60)

    def _all_sources(self) -> list[dict]:
        return [self._normalize(x) for x in self._read_sources_file() if self._normalize(x)["name"]]

    def _sources_by_name(self, sources: list[dict]) -> dict[str, dict]:
        return {s["name"]: s for s in sources}

    def _priority_order(self, sources: list[dict]) -> list[dict]:
        cache = self._load_cache()
        by_name = self._sources_by_name(sources)
        ordered: list[dict] = []

        cached_names = cache.get("success_names", []) if isinstance(cache.get("success_names", []), list) else []
        for name in cached_names:
            if name in by_name:
                ordered.append(by_name.pop(name))

        ordered.extend(by_name.values())
        return ordered

    def _test_source(self, rest_client, source: dict) -> tuple[bool, int]:
        if not source.get("enabled"):
            return False, 0
        if not source.get("path") or not source.get("tr_id"):
            return False, 0

        client = KISRankingClient(rest_client, [source])
        candidates, used_sources = client.fetch_candidates()
        count = len(candidates)
        if self.require_rows:
            return count > 0, count
        return True, count

    def _resolve_active_sources(self, rest_client) -> list[dict]:
        sources = [s for s in self._all_sources() if s.get("enabled")]
        if not sources:
            return []

        cache = self._load_cache()
        by_name = self._sources_by_name(sources)

        if self._cache_is_fresh(cache):
            cached_names = cache.get("success_names", []) if isinstance(cache.get("success_names", []), list) else []
            active = [by_name[name] for name in cached_names if name in by_name]
            if active:
                logger.info("소스관리자 캐시 사용: %s", ", ".join([s["name"] for s in active]))
                return active

        ordered = self._priority_order(sources)
        success_names = []
        success_sources = []

        for source in ordered:
            ok, count = self._test_source(rest_client, source)
            if ok:
                success_names.append(source["name"])
                success_sources.append(source)
                logger.info("소스관리자 성공: %s | 후보수=%s", source["name"], count)
            else:
                logger.warning("소스관리자 실패: %s", source["name"])

        if success_sources:
            self._save_cache({"ts": time.time(), "success_names": success_names})
            return success_sources

        cached_names = cache.get("success_names", []) if isinstance(cache.get("success_names", []), list) else []
        fallback = [by_name[name] for name in cached_names if name in by_name]
        if fallback:
            logger.warning("소스관리자 전체 실패 -> 이전 캐시 fallback 사용")
            return fallback

        return []

    def fetch_candidates(self, rest_client) -> tuple[dict[str, str], list[str]]:
        active_sources = self._resolve_active_sources(rest_client)
        if not active_sources:
            return {}, []

        client = KISRankingClient(rest_client, active_sources)
        candidates, used_sources = client.fetch_candidates()
        return candidates, used_sources
