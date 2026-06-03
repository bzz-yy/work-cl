"""
Coze 工具：缠论多级别分析（单文件版）
======================================
把整个分析流水线塞进一个 handler.py，符合 Coze "一工具一文件" 的约束。

Metadata 配置（在 IDE 元数据面板录入）：

Input：
  symbol         string   必填   股票代码，如 600519.SH

（access_token / lookback_days / levels 已固化在脚本顶部常量，不再走入参）

Output：
  success             boolean
  error               string
  data                object   完整结构化结果
  facts               array<string>   事实清单（给上层 LLM 用）
  narrative_fallback  string   规则拼接的兜底解读
"""
from runtime import Args
from typings.test.test import Input, Output

import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import requests


# ============================================================
# 配置
# ============================================================

BASE_URL = "https://quantapi.51ifind.com/api/v1"

# === 以下为内置固定参数，需要调整直接改这里 ===
ACCESS_TOKEN = "aa62779ad95727b88574d08d2617ee8b406aa15a.signs_NzU1Nzg5MjQ0"
LOOKBACK_DAYS = 30
LEVELS = [5, 15, 60]
# ============================================

LEVEL_LABEL = {5: "5分钟", 15: "15分钟", 60: "60分钟"}


# ============================================================
# 1. 数据拉取（同花顺 iFinD high_frequency 接口）
# ============================================================

def _post(endpoint: str, payload: Dict[str, Any], token: str,
          timeout: int = 30) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "access_token": token}
    r = requests.post(f"{BASE_URL}/{endpoint}", headers=headers,
                      json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("errorcode", -1) != 0:
        raise RuntimeError(
            f"THS接口报错 [{data.get('errorcode')}]: {data.get('errmsg', '未知错误')}"
        )
    return data


def _ths_hf(thscode: str, indicators: str, interval: int,
            start: str, end: str, token: str,
            calculate: Optional[str] = None) -> List[Dict[str, Any]]:
    """通用高频接口调用。
    calculate: 指标分量参数，如 "12,26,9,DIFF" / "12,26,9,DEA" / "12,26,9,MACD"。
    """
    function_para: Dict[str, Any] = {
        "CPS": "forward1", "Fill": "Previous", "Interval": str(interval),
    }
    if calculate:
        function_para["calculate"] = {indicators: calculate}
    payload = {
        "codes": thscode, "indicators": indicators,
        "starttime": start, "endtime": end,
        "functionpara": function_para,
    }
    data = _post("high_frequency", payload, token)
    tables = data.get("tables") or []
    if not tables:
        return []
    t0 = tables[0]
    times = t0.get("time") or []
    table = t0.get("table") or {}
    if not table:
        table = {k: v for k, v in t0.items()
                 if isinstance(v, list) and k != "time"}
    rows: List[Dict[str, Any]] = []
    for i, tm in enumerate(times):
        row: Dict[str, Any] = {"time": tm}
        for k, v in table.items():
            row[k.lower()] = v[i] if i < len(v) else None
        rows.append(row)
    return rows


def _rows_to_map(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """把 [{'time':..., 'macd':...}] 压成 {time: value} 取第一个非空值字段。"""
    m: Dict[str, float] = {}
    for r in rows:
        t = r.get("time")
        if not t:
            continue
        for k, v in r.items():
            if k == "time":
                continue
            if v is not None:
                try:
                    m[t] = float(v)
                except (TypeError, ValueError):
                    pass
                break
    return m


def _ohlc_rows_to_klines(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows:
        t = r.get("time")
        if not t:
            continue
        out.append({
            "time": t,
            "open": float(r.get("open") or 0),
            "high": float(r.get("high") or 0),
            "low": float(r.get("low") or 0),
            "close": float(r.get("close") or 0),
            "dif": 0.0, "dea": 0.0, "macd": 0.0,
        })
    return out


# ----- 本地合并 5min → 15min / 60min -----

# A 股交易时段桶边界（分钟数 = 小时×60 + 分钟）
_BUCKETS_15 = ([9 * 60 + 30 + 15 * k for k in range(1, 9)] +
               [13 * 60 + 15 * k for k in range(1, 9)])
_BUCKETS_60 = [10 * 60 + 30, 11 * 60 + 30, 14 * 60, 15 * 60]


def _bucket_end_time(bar_time: str, target_min: int) -> Optional[str]:
    """给定 5min K 的结束时间，返回它在 target_min 级别下所属桶的结束时间。"""
    try:
        dt = datetime.strptime(bar_time[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    minute_of_day = dt.hour * 60 + dt.minute
    bounds = _BUCKETS_15 if target_min == 15 else (_BUCKETS_60 if target_min == 60 else None)
    if bounds is None:
        return None
    for b in bounds:
        if minute_of_day <= b:
            h2, m2 = divmod(b, 60)
            return dt.replace(hour=h2, minute=m2).strftime("%Y-%m-%d %H:%M")
    return None


def merge_kline(klines_5min: List[Dict[str, Any]], target_min: int) -> List[Dict[str, Any]]:
    """把 5min K 线按交易时段合并为 target_min 级别。仅聚合 OHLC，MACD 置 0。"""
    if target_min == 5:
        return [dict(k) for k in klines_5min]
    buckets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for k in klines_5min:
        bt = _bucket_end_time(k["time"], target_min)
        if not bt:
            continue
        if bt not in buckets:
            buckets[bt] = {
                "time": bt, "open": k["open"], "high": k["high"],
                "low": k["low"], "close": k["close"],
                "dif": 0.0, "dea": 0.0, "macd": 0.0,
            }
            order.append(bt)
        else:
            b = buckets[bt]
            b["high"] = max(b["high"], k["high"])
            b["low"] = min(b["low"], k["low"])
            b["close"] = k["close"]
    return [buckets[t] for t in order]


def attach_macd(klines: List[Dict[str, Any]],
                dif_rows: List[Dict[str, Any]],
                dea_rows: List[Dict[str, Any]],
                macd_rows: List[Dict[str, Any]]) -> None:
    dif_m, dea_m, macd_m = _rows_to_map(dif_rows), _rows_to_map(dea_rows), _rows_to_map(macd_rows)
    for k in klines:
        t = k["time"]
        # 兼容 MACD 接口返回的时间精度差异（带秒/不带秒）
        k["dif"] = dif_m.get(t, dif_m.get(t[:16], 0.0))
        k["dea"] = dea_m.get(t, dea_m.get(t[:16], 0.0))
        k["macd"] = macd_m.get(t, macd_m.get(t[:16], 0.0))


def fetch_levels(thscode: str, lookback: int, levels: List[int],
                 token: str) -> Dict[int, List[Dict[str, Any]]]:
    """统一拉取：5min OHLC 只拉一次本地合并；每个级别独立拉 DIF/DEA/MACD。
    共消耗 indicator 字段数 = 4 + 3 * len(levels)。
    """
    start, end = time_range(lookback)
    # 1) 5min OHLC（只拉一次）
    ohlc5_rows = _ths_hf(thscode, "open,high,low,close", 5, start, end, token)
    klines_5_base = _ohlc_rows_to_klines(ohlc5_rows)

    out: Dict[int, List[Dict[str, Any]]] = {}
    for lv in levels:
        # OHLC：5min 直接用，15/60 本地合并
        klines = [dict(k) for k in klines_5_base] if lv == 5 else merge_kline(klines_5_base, lv)
        # MACD：每个级别单独拉
        dif_rows = _ths_hf(thscode, "MACD", lv, start, end, token, calculate="12,26,9,DIFF")
        dea_rows = _ths_hf(thscode, "MACD", lv, start, end, token, calculate="12,26,9,DEA")
        macd_rows = _ths_hf(thscode, "MACD", lv, start, end, token, calculate="12,26,9,MACD")
        attach_macd(klines, dif_rows, dea_rows, macd_rows)
        out[lv] = klines
    return out


def time_range(lookback_days: int) -> Tuple[str, str]:
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days)
    return (start_dt.strftime("%Y-%m-%d 09:15:00"),
            end_dt.strftime("%Y-%m-%d 15:15:00"))


# ============================================================
# 2. 缠论算法
# ============================================================

def process_inclusion(klines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """K线包含关系处理。"""
    if not klines:
        return []
    out: List[Dict[str, Any]] = []
    direction = 0
    for i, k in enumerate(klines):
        cur = {"high": k["high"], "low": k["low"],
               "time": k["time"], "idx_range": [i, i]}
        if not out:
            out.append(cur)
            continue
        prev = out[-1]
        included = (prev["high"] >= cur["high"] and prev["low"] <= cur["low"]) or \
                   (prev["high"] <= cur["high"] and prev["low"] >= cur["low"])
        if not included:
            if cur["high"] > prev["high"]:
                direction = 1
            elif cur["low"] < prev["low"]:
                direction = -1
            out.append(cur)
        else:
            if direction >= 0:
                prev["high"] = max(prev["high"], cur["high"])
                prev["low"] = max(prev["low"], cur["low"])
            else:
                prev["high"] = min(prev["high"], cur["high"])
                prev["low"] = min(prev["low"], cur["low"])
            prev["time"] = cur["time"]
            prev["idx_range"][1] = i
    return out


def find_fenxing(processed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """识别顶/底分型。"""
    fx: List[Dict[str, Any]] = []
    for i in range(1, len(processed) - 1):
        a, b, c = processed[i - 1], processed[i], processed[i + 1]
        if b["high"] > a["high"] and b["high"] > c["high"] and \
           b["low"] > a["low"] and b["low"] > c["low"]:
            fx.append({"type": "top", "pk_idx": i, "price": b["high"],
                       "time": b["time"], "k_idx": b["idx_range"][1]})
        elif b["low"] < a["low"] and b["low"] < c["low"] and \
             b["high"] < a["high"] and b["high"] < c["high"]:
            fx.append({"type": "bot", "pk_idx": i, "price": b["low"],
                       "time": b["time"], "k_idx": b["idx_range"][1]})
    return fx


def build_bi(fx_list: List[Dict[str, Any]], min_k_gap: int = 4) -> List[Dict[str, Any]]:
    """严格笔。"""
    if len(fx_list) < 2:
        return []
    confirmed: List[Dict[str, Any]] = [fx_list[0]]
    for fx in fx_list[1:]:
        last = confirmed[-1]
        if fx["type"] == last["type"]:
            if (fx["type"] == "top" and fx["price"] > last["price"]) or \
               (fx["type"] == "bot" and fx["price"] < last["price"]):
                confirmed[-1] = fx
            continue
        if (fx["pk_idx"] - last["pk_idx"]) < min_k_gap:
            continue
        if last["type"] == "bot" and fx["type"] == "top" and fx["price"] <= last["price"]:
            continue
        if last["type"] == "top" and fx["type"] == "bot" and fx["price"] >= last["price"]:
            continue
        confirmed.append(fx)
    bis: List[Dict[str, Any]] = []
    for i in range(len(confirmed) - 1):
        a, b = confirmed[i], confirmed[i + 1]
        direction = "up" if a["type"] == "bot" else "down"
        bis.append({
            "dir": direction,
            "start_fx": a, "end_fx": b,
            "start_time": a["time"], "end_time": b["time"],
            "low": min(a["price"], b["price"]),
            "high": max(a["price"], b["price"]),
            "k_start": a["k_idx"], "k_end": b["k_idx"],
        })
    return bis


def bi_macd_metrics(bi: Dict[str, Any], klines: List[Dict[str, Any]]) -> Dict[str, float]:
    s, e = bi["k_start"], bi["k_end"]
    seg = klines[s:e + 1]
    if not seg:
        return {"macd_area": 0.0, "dif_peak": 0.0, "dea_avg": 0.0}
    if bi["dir"] == "up":
        area = sum(max(0.0, k.get("macd", 0.0) or 0.0) for k in seg)
        difs = [k.get("dif", 0.0) or 0.0 for k in seg]
        dif_peak = max(difs) if difs else 0.0
    else:
        area = sum(abs(min(0.0, k.get("macd", 0.0) or 0.0)) for k in seg)
        difs = [k.get("dif", 0.0) or 0.0 for k in seg]
        dif_peak = min(difs) if difs else 0.0
    deas = [k.get("dea", 0.0) or 0.0 for k in seg]
    dea_avg = sum(deas) / len(deas) if deas else 0.0
    return {"macd_area": area, "dif_peak": dif_peak, "dea_avg": dea_avg}


def annotate_bi_with_macd(bis, klines):
    for bi in bis:
        bi["macd"] = bi_macd_metrics(bi, klines)


def detect_divergence(bis: List[Dict[str, Any]], area_threshold: float = 0.8):
    events = []
    for i in range(2, len(bis)):
        cur, prev = bis[i], bis[i - 2]
        if cur["dir"] != prev["dir"]:
            continue
        cm = cur.get("macd", {})
        pm = prev.get("macd", {})
        new_extreme = (cur["dir"] == "up" and cur["high"] > prev["high"]) or \
                      (cur["dir"] == "down" and cur["low"] < prev["low"])
        if not new_extreme:
            continue
        pm_area = pm.get("macd_area", 0)
        cm_area = cm.get("macd_area", 0)
        # 前笔无 MACD 力度则无法做力度比较，跳过避免假信号
        if pm_area <= 0:
            continue
        area_shrink = cm_area < pm_area * area_threshold
        if cur["dir"] == "up":
            dif_ok = cm.get("dif_peak", 0) <= pm.get("dif_peak", 0)
            kind = "top_divergence"
        else:
            dif_ok = cm.get("dif_peak", 0) >= pm.get("dif_peak", 0)
            kind = "bot_divergence"
        if area_shrink and dif_ok:
            cur["divergence"] = {
                "kind": kind, "vs_bi_index": i - 2,
                "area_ratio": round(cm_area / pm_area, 3),
                "dea_zone": "above_zero" if cm.get("dea_avg", 0) > 0 else "below_zero",
            }
            events.append({"bi_index": i, **cur["divergence"]})
    return events


def build_bi_zhongshu(bis: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(bis) < 3:
        return []
    zss: List[Dict[str, Any]] = []
    i = 0
    while i <= len(bis) - 3:
        b1, b2, b3 = bis[i], bis[i + 1], bis[i + 2]
        if b1["dir"] == b3["dir"] and b1["dir"] != b2["dir"]:
            zg = min(b1["high"], b3["high"])
            zd = max(b1["low"], b3["low"])
            if zg > zd:
                hi = max(b1["high"], b2["high"], b3["high"])
                lo = min(b1["low"], b2["low"], b3["low"])
                end = i + 2
                status = "formed"
                j = i + 3
                while j < len(bis):
                    bj = bis[j]
                    if bj["high"] >= zd and bj["low"] <= zg:
                        end = j
                        hi = max(hi, bj["high"])
                        lo = min(lo, bj["low"])
                        j += 1
                    else:
                        status = "broken"
                        break
                if j >= len(bis) and status != "broken":
                    status = "extending"
                zss.append({
                    "start_bi": i, "end_bi": end,
                    "zg": zg, "zd": zd, "high": hi, "low": lo,
                    "bi_count": end - i + 1, "status": status,
                    "start_time": b1["start_time"],
                    "end_time": bis[end]["end_time"],
                })
                i = end + 1
                continue
        i += 1
    return zss


def position_vs_zhongshu(last_price: float, zs: Dict[str, Any]) -> str:
    if last_price > zs["zg"]:
        return "above"
    if last_price < zs["zd"]:
        return "below"
    return "inside"


def realtime_alert(klines_closed, realtime_bar, bis, bar_minutes):
    if not realtime_bar or not bis or len(klines_closed) < 2:
        return None
    last_bi = bis[-1]
    prev_closed = klines_closed[-1]
    scenarios = []
    if last_bi["dir"] == "up":
        scenarios.append({
            "scenario": f"若本根 K 最低 < {prev_closed['low']:.2f}",
            "result": "可能形成顶分型，上升笔结束",
            "next_action": "等待底分型确认转折，关注下方支撑",
        })
        scenarios.append({
            "scenario": f"若本根 K 最高 > {last_bi['high']:.2f}",
            "result": "延续上升笔，未触发分型",
            "next_action": "继续持有，关注上方阻力与背驰",
        })
    else:
        scenarios.append({
            "scenario": f"若本根 K 最高 > {prev_closed['high']:.2f}",
            "result": "可能形成底分型，下降笔结束",
            "next_action": "等待顶分型确认转折，关注上方阻力",
        })
        scenarios.append({
            "scenario": f"若本根 K 最低 < {last_bi['low']:.2f}",
            "result": "延续下降笔，未触发分型",
            "next_action": "继续观望，留意背驰与一类买点",
        })
    return {"current_bar": realtime_bar, "bar_minutes": bar_minutes,
            "potential_impact": scenarios}


def _bi_summary(bi):
    if not bi:
        return None
    m = bi.get("macd", {})
    return {
        "dir": bi["dir"], "start_time": bi["start_time"], "end_time": bi["end_time"],
        "low": bi["low"], "high": bi["high"],
        "macd_area": round(m.get("macd_area", 0), 3),
        "dif_peak": round(m.get("dif_peak", 0), 4),
        "dea_avg": round(m.get("dea_avg", 0), 4),
        "divergence": bi.get("divergence"),
    }


def _zs_summary(zs):
    if not zs:
        return None
    return {
        "zg": zs["zg"], "zd": zs["zd"], "high": zs["high"], "low": zs["low"],
        "bi_count": zs["bi_count"], "status": zs["status"],
        "start_time": zs["start_time"], "end_time": zs["end_time"],
    }


def _is_session_close_bar(bar_time: str) -> bool:
    """K 线结束时间是否正好是 A 股交易时段收盘（15:00）。"""
    try:
        return bar_time[-5:] == "15:00"
    except Exception:
        return False


def analyze_level(klines, bar_minutes, include_unclosed=False):
    if not klines:
        return {"error": "no data", "bar_count": 0}
    last = klines[-1] if klines else None
    # 收盘后跑：最后一根 K 时间为 15:00 → 视为已收 K，全量进入分析
    last_is_closed = bool(last and _is_session_close_bar(last.get("time", "")))
    if include_unclosed or last_is_closed:
        closed = klines
        realtime_bar = None
    else:
        closed = klines[:-1] if klines else []
        realtime_bar = klines[-1] if klines else None
    if len(closed) < 10:
        return {"error": "insufficient data", "bar_count": len(closed),
                "realtime_bar": realtime_bar}
    processed = process_inclusion(closed)
    fx = find_fenxing(processed)
    bis = build_bi(fx)
    annotate_bi_with_macd(bis, closed)
    divs = detect_divergence(bis)
    zss = build_bi_zhongshu(bis)

    last_close = closed[-1]["close"]
    last_zs = zss[-1] if zss else None
    last_bi = bis[-1] if bis else None
    pos = position_vs_zhongshu(last_close, last_zs) if last_zs else None
    rt = realtime_alert(closed, realtime_bar, bis, bar_minutes)

    trend = "unknown"
    if len(bis) >= 3:
        up_count = sum(1 for b in bis[-4:] if b["dir"] == "up")
        trend = "up" if up_count >= 3 else ("down" if up_count <= 1 else "range")

    if len(bis) >= 6 and zss:
        confidence = "high"
    elif len(bis) >= 4:
        confidence = "mid"
    else:
        confidence = "low"

    return {
        "bar_count": len(closed), "bi_count": len(bis), "zhongshu_count": len(zss),
        "trend": trend, "confidence": confidence,
        "last_bi": _bi_summary(last_bi),
        "last_zhongshu": _zs_summary(last_zs),
        "position_vs_zs": pos, "last_close": last_close,
        "divergences": [{"bi_index": e["bi_index"], "kind": e["kind"],
                         "area_ratio": round(e["area_ratio"], 3),
                         "dea_zone": e["dea_zone"]} for e in divs[-3:]],
        "realtime_alert": rt,
        "all_bi_summary": [_bi_summary(b) for b in bis[-6:]],
        "all_zs_summary": [_zs_summary(z) for z in zss[-2:]],
    }


# ============================================================
# 3. 事实清单 + 兜底解读
# ============================================================

def build_facts(symbol: str, by_level: Dict[int, Dict[str, Any]]) -> List[str]:
    facts: List[str] = [f"标的：{symbol}"]
    for lv in sorted(by_level.keys(), reverse=True):
        r = by_level[lv]
        lbl = LEVEL_LABEL.get(lv, f"{lv}分钟")
        if r.get("error"):
            facts.append(f"[{lbl}] 数据不足：{r.get('error')}（共 {r.get('bar_count', 0)} 根K）。")
            continue
        facts.append(f"[{lbl}] 共 {r['bar_count']} 根K，识别 {r['bi_count']} 笔、"
                     f"{r['zhongshu_count']} 个笔中枢，置信度 {r['confidence']}。")
        facts.append(f"[{lbl}] 当前价 {r['last_close']}，趋势：{r['trend']}。")
        lb = r.get("last_bi")
        if lb:
            facts.append(f"[{lbl}] 最近一笔方向 {lb['dir']}，"
                         f"{lb['start_time']}→{lb['end_time']}，区间 [{lb['low']}, {lb['high']}]。")
            facts.append(f"[{lbl}] 最近一笔 MACD：柱面积 {lb['macd_area']}，"
                         f"DIF 峰值 {lb['dif_peak']}，DEA 均值 {lb['dea_avg']}"
                         f"（{'0轴上方' if lb['dea_avg']>0 else '0轴下方'}）。")
            if lb.get("divergence"):
                d = lb["divergence"]
                facts.append(f"[{lbl}] 最近一笔出现 {d['kind']}，"
                             f"力度比 {d['area_ratio']}，位于 {d['dea_zone']}。")
        lz = r.get("last_zhongshu")
        if lz:
            facts.append(f"[{lbl}] 最近笔中枢 [{lz['zd']}, {lz['zg']}]，"
                         f"扩展 [{lz['low']}, {lz['high']}]，含 {lz['bi_count']} 笔，状态：{lz['status']}。")
            pos = r.get("position_vs_zs")
            if pos:
                m = {"above": "中枢上方", "below": "中枢下方", "inside": "中枢内部"}
                facts.append(f"[{lbl}] 当前价位于{m.get(pos, pos)}。")
        else:
            facts.append(f"[{lbl}] 暂未形成笔中枢（笔数 {r['bi_count']}）。")
        rt = r.get("realtime_alert")
        if rt and rt.get("potential_impact"):
            for s in rt["potential_impact"]:
                facts.append(f"[{lbl}-实时] {s['scenario']} → {s['result']}；建议：{s['next_action']}。")
    return facts


def _pos_cn(p):
    return {"above": "中枢上方", "below": "中枢下方", "inside": "中枢内部"}.get(p or "", "未知")


def collect_signals(by_level):
    out = []
    r60, r15, r5 = by_level.get(60), by_level.get(15), by_level.get(5)
    if r60 and not r60.get("error"):
        d60 = (r60.get("last_bi") or {}).get("divergence") or {}
        if d60.get("kind") == "bot_divergence":
            out.append("60分钟出现底背驰，潜在一类买点；结合 15min/5min 寻找精确进场。")
        if d60.get("kind") == "top_divergence":
            out.append("60分钟出现顶背驰，潜在一类卖点；注意减仓或离场。")
    if r15 and not r15.get("error"):
        lz15 = r15.get("last_zhongshu")
        pos15 = r15.get("position_vs_zs")
        if lz15 and pos15 == "above" and lz15.get("status") in ("formed", "extending"):
            out.append(f"15分钟价位于中枢[{lz15['zd']},{lz15['zg']}]上方，回踩不破上沿可视为三类买点。")
        if lz15 and pos15 == "below" and lz15.get("status") in ("formed", "extending"):
            out.append(f"15分钟价位于中枢[{lz15['zd']},{lz15['zg']}]下方，反抽不过下沿可视为三类卖点。")
    if r5 and not r5.get("error"):
        d5 = (r5.get("last_bi") or {}).get("divergence") or {}
        if d5.get("kind") == "bot_divergence":
            out.append("5分钟出现底背驰，短线进场窗口；止损放笔的最低点。")
        if d5.get("kind") == "top_divergence":
            out.append("5分钟出现顶背驰，短线减仓窗口。")
    return out


def build_watchlist(by_level):
    out = []
    r5 = by_level.get(5)
    if r5 and not r5.get("error"):
        rt = r5.get("realtime_alert")
        if rt:
            for s in rt.get("potential_impact", []):
                out.append(f"5分钟：{s['scenario']} → {s['result']}")
    r15 = by_level.get(15)
    if r15 and not r15.get("error"):
        lz = r15.get("last_zhongshu")
        if lz:
            out.append(f"15分钟中枢上沿 {lz['zg']}，下沿 {lz['zd']}，关注突破/跌破。")
    r60 = by_level.get(60)
    if r60 and not r60.get("error"):
        lb = r60.get("last_bi")
        if lb:
            extreme = lb["high"] if lb["dir"] == "up" else lb["low"]
            out.append(f"60分钟最近一笔极值 {extreme}，关注是否被突破/破坏。")
    return out


def build_narrative_fallback(symbol, by_level, as_of):
    parts = [f"【{symbol} | {as_of}】"]
    r60 = by_level.get(60)
    if r60 and not r60.get("error"):
        seg = ["", "【大方向（60分钟）】", f"整体趋势：{r60['trend']}，置信度 {r60['confidence']}。"]
        lb = r60.get("last_bi")
        if lb:
            seg.append(f"最近一笔向{'上' if lb['dir']=='up' else '下'}，区间 {lb['low']}~{lb['high']}。")
            if lb.get("divergence"):
                d = lb["divergence"]
                seg.append(f"该笔出现{'顶' if d['kind']=='top_divergence' else '底'}背驰"
                           f"（力度比 {d['area_ratio']}，{d['dea_zone']}）。")
        lz = r60.get("last_zhongshu")
        if lz:
            seg.append(f"最近中枢 [{lz['zd']}, {lz['zg']}]，状态 {lz['status']}，"
                       f"当前价位于{_pos_cn(r60.get('position_vs_zs'))}。")
        else:
            seg.append("尚未形成笔中枢，仅作方向参考。")
        parts.append(" ".join(seg))
    r15 = by_level.get(15)
    if r15 and not r15.get("error"):
        seg = ["", "【中级结构（15分钟）】", f"趋势 {r15['trend']}，置信度 {r15['confidence']}。"]
        lz = r15.get("last_zhongshu")
        if lz:
            seg.append(f"中枢 [{lz['zd']}, {lz['zg']}]（{lz['status']}），"
                       f"当前价{_pos_cn(r15.get('position_vs_zs'))}。")
        lb = r15.get("last_bi")
        if lb and lb.get("divergence"):
            d = lb["divergence"]
            seg.append(f"最近一笔出现{'顶' if d['kind']=='top_divergence' else '底'}背驰。")
        parts.append(" ".join(seg))
    r5 = by_level.get(5)
    if r5 and not r5.get("error"):
        seg = ["", "【小级别触发（5分钟）】"]
        lb = r5.get("last_bi")
        if lb:
            seg.append(f"最近一笔方向{'上' if lb['dir']=='up' else '下'}，"
                       f"{lb['start_time']} → {lb['end_time']}。")
        rt = r5.get("realtime_alert")
        if rt and rt.get("potential_impact"):
            seg.append("实时观察：")
            for s in rt["potential_impact"]:
                seg.append(f"· {s['scenario']} → {s['result']}（{s['next_action']}）")
        parts.append("\n".join(seg))
    signals = collect_signals(by_level)
    parts.append("")
    if signals:
        parts.append("【综合信号】")
        for sg in signals:
            parts.append(f"· {sg}")
    else:
        parts.append("【综合信号】当前未匹配明确买卖点，建议观望。")
    watch = build_watchlist(by_level)
    if watch:
        parts.append("")
        parts.append("【接下来重点观察】")
        for w in watch:
            parts.append(f"· {w}")
    return "\n".join(parts)


# ============================================================
# 4. handler 入口
# ============================================================

def handler(args: Args[Input]) -> Output:
    log = getattr(args, "logger", None)
    inp = args.input

    symbol = (getattr(inp, "symbol", None) or "").strip()
    if not symbol:
        return {"success": False, "error": "symbol is required",
                "data": {}, "facts": [], "narrative_fallback": ""}

    token = ACCESS_TOKEN
    lookback = LOOKBACK_DAYS
    levels = LEVELS

    if log:
        log.info(f"fetch {symbol} levels={levels} lookback={lookback}d")

    by_level: Dict[int, Dict[str, Any]] = {}
    fetch_errors: List[str] = []
    try:
        all_klines = fetch_levels(symbol, lookback, levels, token=token)
    except Exception as e:
        msg = f"fetch failed: {e}"
        fetch_errors.append(msg)
        if log:
            log.error(msg)
        all_klines = {lv: [] for lv in levels}

    for lv in levels:
        try:
            klines = all_klines.get(lv, [])
            if log:
                log.info(f"level {lv}: {len(klines)} bars")
            by_level[lv] = analyze_level(klines, bar_minutes=lv, include_unclosed=False)
        except Exception as e:
            msg = f"level {lv} analyze failed: {e}"
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
            "symbol": symbol, "as_of": as_of, "lookback_days": lookback,
            "levels": {str(k): v for k, v in by_level.items()},
        },
        "facts": facts,
        "narrative_fallback": narrative,
    }
