from pathlib import Path

def parse_category_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result

    current_category = "관심종목"
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
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
        if len(code) == 6 and code.isdigit():
            result[code] = current_category
    return result

def category_for(code: str, category_map: dict[str, str], default: str = "관심종목") -> str:
    return category_map.get(code, default)
