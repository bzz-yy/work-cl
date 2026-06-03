"""
handler.py — Coze 插件入口

在 Coze IDE 的 Metadata 里配置 Input/Output（见 METADATA.md），
typings 由平台自动生成。

Input 字段：
  symbol: str          必填，如 "600519.SH"
  access_token: str    选填，留空用 DEFAULT_TOKEN
  lookback_days: int   选填，默认 30
  levels: str          选填，默认 "5,15,60"，逗号分隔

Output 字段：
  success: bool
  error: str
  data: dict           三级别完整结构化结果
  facts: list[str]     事实清单
  narrative_fallback: str  规则拼接的兜底解读
"""
from runtime import Args
from typings.test.test import Input, Output

from datetime import datetime

from fetcher import fetch_kline_with_macd, time_range, DEFAULT_TOKEN
from chan_core import analyze_level
from narrative import build_facts, build_narrative_fallback


def handler(args: Args[Input]) -> Output:
    log = getattr(args, "logger", None)
    inp = args.input

    symbol = (getattr(inp, "symbol", None) or "").strip()
    if not symbol:
        return {
            "success": False, "error": "symbol is required",
            "data": {}, "facts": [], "narrative_fallback": "",
        }

    token = (getattr(inp, "access_token", None) or "").strip() or DEFAULT_TOKEN
    lookback = int(getattr(inp, "lookback_days", 0) or 30)
    levels_str = (getattr(inp, "levels", None) or "5,15,60").strip()
    try:
        levels = [int(x.strip()) for x in levels_str.split(",") if x.strip()]
    except ValueError:
        levels = [5, 15, 60]

    start, end = time_range(lookback)
    if log:
        log.info(f"fetch {symbol} levels={levels} {start}~{end}")

    by_level = {}
    fetch_errors = []
    for lv in levels:
        try:
            klines = fetch_kline_with_macd(symbol, lv, start, end, token=token)
            if log:
                log.info(f"level {lv}: {len(klines)} bars")
            result = analyze_level(klines, bar_minutes=lv, include_unclosed=False)
            by_level[lv] = result
        except Exception as e:
            msg = f"level {lv} fetch/analyze failed: {e}"
            fetch_errors.append(msg)
            if log:
                log.error(msg)
            by_level[lv] = {"error": str(e), "bar_count": 0}

    as_of = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    facts = build_facts(symbol, by_level)
    narrative = build_narrative_fallback(symbol, by_level, as_of)

    return {
        "success": len(fetch_errors) < len(levels),
        "error": "; ".join(fetch_errors),
        "data": {
            "symbol": symbol,
            "as_of": as_of,
            "lookback_days": lookback,
            "levels": {str(k): v for k, v in by_level.items()},
        },
        "facts": facts,
        "narrative_fallback": narrative,
    }
