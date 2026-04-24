from pathlib import Path

def parse_code_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        code = parts[0]
        name = " ".join(parts[1:]) if len(parts) > 1 else code
        if len(code) == 6 and code.isdigit():
            result[code] = name
    return result