def safe_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default

def safe_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default

def mean(values):
    return (sum(values) / len(values)) if values else 0.0

def format_number(n: int) -> str:
    return f"{n:,}"