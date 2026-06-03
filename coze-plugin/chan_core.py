"""
chan_core.py
============
缠论核心算法（纯标准库实现，不依赖 pandas/numpy）。

数据流：
  原始K线列表 -> K线包含处理 -> 分型 -> 笔 -> 中枢 -> 背驰 -> 事实清单

每根 K 的字典结构：
  {"time": "2026-06-03 14:25", "open":..,"high":..,"low":..,"close":..,
   "dif":..,"dea":..,"macd":..}
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple


# ---------- 1. K线包含处理 ----------

def process_inclusion(klines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """K线包含关系处理。返回处理后K线，每根带 idx_range 指向原始K起止下标。"""
    if not klines:
        return []
    out: List[Dict[str, Any]] = []
    direction = 0  # 1 上, -1 下, 0 未定
    for i, k in enumerate(klines):
        cur = {
            "high": k["high"], "low": k["low"],
            "time": k["time"], "idx_range": [i, i],
        }
        if not out:
            out.append(cur)
            continue
        prev = out[-1]
        # 判断包含
        included = (prev["high"] >= cur["high"] and prev["low"] <= cur["low"]) or \
                   (prev["high"] <= cur["high"] and prev["low"] >= cur["low"])
        if not included:
            # 更新方向
            if cur["high"] > prev["high"]:
                direction = 1
            elif cur["low"] < prev["low"]:
                direction = -1
            out.append(cur)
        else:
            # 合并
            if direction >= 0:
                merged_high = max(prev["high"], cur["high"])
                merged_low = max(prev["low"], cur["low"])
            else:
                merged_high = min(prev["high"], cur["high"])
                merged_low = min(prev["low"], cur["low"])
            prev["high"] = merged_high
            prev["low"] = merged_low
            prev["time"] = cur["time"]
            prev["idx_range"][1] = i
    return out


# ---------- 2. 分型 ----------

def find_fenxing(processed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """识别顶/底分型。处理后K线 i 满足 K[i-1]<K[i]>K[i+1] 为顶，反之为底。
    返回: [{"type":"top/bot","pk_idx":i,"price":..,"time":..,"k_idx":原始K下标}]
    """
    fx: List[Dict[str, Any]] = []
    n = len(processed)
    for i in range(1, n - 1):
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


# ---------- 3. 笔（严格笔） ----------

def build_bi(fx_list: List[Dict[str, Any]],
             processed: List[Dict[str, Any]],
             min_k_gap: int = 4) -> List[Dict[str, Any]]:
    """严格笔：相邻顶底分型间至少 min_k_gap 根处理后K线，类型必须交替。
    遇同向更极端分型时替换前一分型，否则确认新笔。
    返回: [{"dir":"up/down","start_fx":{...},"end_fx":{...},
            "start_time":..,"end_time":..,"low":..,"high":..,
            "k_start":..,"k_end":..}]
    """
    if len(fx_list) < 2:
        return []
    bis: List[Dict[str, Any]] = []
    confirmed: List[Dict[str, Any]] = [fx_list[0]]
    for fx in fx_list[1:]:
        last = confirmed[-1]
        if fx["type"] == last["type"]:
            # 同向：更极端则替换
            if (fx["type"] == "top" and fx["price"] > last["price"]) or \
               (fx["type"] == "bot" and fx["price"] < last["price"]):
                confirmed[-1] = fx
            continue
        # 异向：检查间距
        if (fx["pk_idx"] - last["pk_idx"]) < min_k_gap:
            continue
        # 价格逻辑校验
        if last["type"] == "bot" and fx["type"] == "top" and fx["price"] <= last["price"]:
            continue
        if last["type"] == "top" and fx["type"] == "bot" and fx["price"] >= last["price"]:
            continue
        confirmed.append(fx)
    # 拼笔
    for i in range(len(confirmed) - 1):
        a, b = confirmed[i], confirmed[i + 1]
        direction = "up" if a["type"] == "bot" else "down"
        low = min(a["price"], b["price"])
        high = max(a["price"], b["price"])
        bis.append({
            "dir": direction,
            "start_fx": a, "end_fx": b,
            "start_time": a["time"], "end_time": b["time"],
            "low": low, "high": high,
            "k_start": a["k_idx"], "k_end": b["k_idx"],
        })
    return bis


# ---------- 4. MACD 力度 ----------

def bi_macd_metrics(bi: Dict[str, Any],
                    klines: List[Dict[str, Any]]) -> Dict[str, float]:
    """计算单笔的 MACD 指标：柱面积、DIF 极值、DEA 均值。"""
    s, e = bi["k_start"], bi["k_end"]
    seg = klines[s:e + 1]
    if not seg:
        return {"macd_area": 0.0, "dif_peak": 0.0, "dea_avg": 0.0}
    # 柱面积：上升笔取红柱(>0)；下降笔取绿柱(<0) 的绝对值
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


def annotate_bi_with_macd(bis: List[Dict[str, Any]],
                          klines: List[Dict[str, Any]]) -> None:
    for bi in bis:
        bi["macd"] = bi_macd_metrics(bi, klines)


# ---------- 5. 背驰 ----------

def detect_divergence(bis: List[Dict[str, Any]],
                      area_threshold: float = 0.8) -> List[Dict[str, Any]]:
    """对每对同向相邻笔（中间隔一笔反向）做背驰判定。
    在 bi 上写入 divergence 字段。返回背驰事件列表。
    """
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
        area_shrink = cm.get("macd_area", 0) < pm.get("macd_area", 0) * area_threshold
        if cur["dir"] == "up":
            dif_ok = cm.get("dif_peak", 0) <= pm.get("dif_peak", 0)
            kind = "top_divergence"
        else:
            dif_ok = cm.get("dif_peak", 0) >= pm.get("dif_peak", 0)
            kind = "bot_divergence"
        if area_shrink and dif_ok:
            cur["divergence"] = {
                "kind": kind,
                "vs_bi_index": i - 2,
                "area_ratio": cm.get("macd_area", 0) / pm["macd_area"] if pm.get("macd_area") else 0,
                "dea_zone": "above_zero" if cm.get("dea_avg", 0) > 0 else "below_zero",
            }
            events.append({"bi_index": i, **cur["divergence"]})
    return events


# ---------- 6. 笔中枢 ----------

def build_bi_zhongshu(bis: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """笔中枢：连续 3 笔（方向 A-B-A）的重叠区间。
    返回 [{"start_bi":i,"end_bi":j,"zg":..,"zd":..,"status":"formed/extending/broken",
           "high":..,"low":..,"bi_count":..}]
    """
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
                # 中枢成立，向后延伸
                hi, lo = max(b1["high"], b2["high"], b3["high"]), min(b1["low"], b2["low"], b3["low"])
                end = i + 2
                status = "formed"
                j = i + 3
                while j < len(bis):
                    bj = bis[j]
                    # 延伸：笔与中枢区间有交集
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
                    "zg": zg, "zd": zd,
                    "high": hi, "low": lo,
                    "bi_count": end - i + 1,
                    "status": status,
                    "start_time": b1["start_time"],
                    "end_time": bis[end]["end_time"],
                })
                i = end + 1
                continue
        i += 1
    return zss


# ---------- 7. 当前位置评估 ----------

def position_vs_zhongshu(last_price: float, zs: Dict[str, Any]) -> str:
    if last_price > zs["zg"]:
        return "above"
    if last_price < zs["zd"]:
        return "below"
    return "inside"


# ---------- 8. 实时未收K潜在影响 ----------

def realtime_alert(klines_closed: List[Dict[str, Any]],
                   realtime_bar: Optional[Dict[str, Any]],
                   bis: List[Dict[str, Any]],
                   bar_minutes: int) -> Optional[Dict[str, Any]]:
    """根据最后一笔方向，推断当前未收K可能造成的结构变化。"""
    if not realtime_bar or not bis or len(klines_closed) < 2:
        return None
    last_bi = bis[-1]
    # 取处理后K线最后一根的高低做参照（粗略：用最后一根已收K）
    prev_closed = klines_closed[-1]
    scenarios = []
    if last_bi["dir"] == "up":
        # 若实时低 < 前K低，可能形成顶分型 → 破坏上升笔
        trigger = prev_closed["low"]
        scenarios.append({
            "scenario": f"若本根 K 最低 < {trigger:.2f}",
            "result": "可能形成顶分型，上升笔结束",
            "next_action": "等待底分型确认转折，关注下方支撑",
        })
        scenarios.append({
            "scenario": f"若本根 K 最高 > {last_bi['high']:.2f}",
            "result": "延续上升笔，未触发分型",
            "next_action": "继续持有，关注上方阻力与背驰",
        })
    else:
        trigger = prev_closed["high"]
        scenarios.append({
            "scenario": f"若本根 K 最高 > {trigger:.2f}",
            "result": "可能形成底分型，下降笔结束",
            "next_action": "等待顶分型确认转折，关注上方阻力",
        })
        scenarios.append({
            "scenario": f"若本根 K 最低 < {last_bi['low']:.2f}",
            "result": "延续下降笔，未触发分型",
            "next_action": "继续观望，留意背驰与一类买点",
        })
    return {
        "current_bar": realtime_bar,
        "bar_minutes": bar_minutes,
        "potential_impact": scenarios,
    }


# ---------- 9. 单级别分析 ----------

def analyze_level(klines: List[Dict[str, Any]],
                  bar_minutes: int,
                  include_unclosed: bool = False) -> Dict[str, Any]:
    """单级别完整分析：返回该级别的结构化结果。
    klines 已按时间升序，含 dif/dea/macd 字段；最后一根可能是未收K。
    """
    if not klines:
        return {"error": "no data"}
    # 分离未收K
    if include_unclosed:
        closed = klines
        realtime_bar = None
    else:
        closed = klines[:-1] if klines else []
        realtime_bar = klines[-1] if klines else None
    if len(closed) < 10:
        return {
            "error": "insufficient data",
            "bar_count": len(closed),
            "realtime_bar": realtime_bar,
        }
    processed = process_inclusion(closed)
    fx = find_fenxing(processed)
    bis = build_bi(fx, processed)
    annotate_bi_with_macd(bis, closed)
    divs = detect_divergence(bis)
    zss = build_bi_zhongshu(bis)

    last_close = closed[-1]["close"]
    last_zs = zss[-1] if zss else None
    last_bi = bis[-1] if bis else None
    pos = position_vs_zhongshu(last_close, last_zs) if last_zs else None
    rt_alert = realtime_alert(closed, realtime_bar, bis, bar_minutes)

    # 趋势判定（粗略）
    trend = "unknown"
    if len(bis) >= 3:
        up_count = sum(1 for b in bis[-4:] if b["dir"] == "up")
        if up_count >= 3:
            trend = "up"
        elif up_count <= 1:
            trend = "down"
        else:
            trend = "range"

    confidence = "low"
    if len(bis) >= 6 and zss:
        confidence = "high"
    elif len(bis) >= 4:
        confidence = "mid"

    return {
        "bar_count": len(closed),
        "bi_count": len(bis),
        "zhongshu_count": len(zss),
        "trend": trend,
        "confidence": confidence,
        "last_bi": _bi_summary(last_bi),
        "last_zhongshu": _zs_summary(last_zs),
        "position_vs_zs": pos,
        "last_close": last_close,
        "divergences": [{"bi_index": e["bi_index"], "kind": e["kind"],
                         "area_ratio": round(e["area_ratio"], 3),
                         "dea_zone": e["dea_zone"]} for e in divs[-3:]],
        "realtime_alert": rt_alert,
        "all_bi_summary": [_bi_summary(b) for b in bis[-6:]],
        "all_zs_summary": [_zs_summary(z) for z in zss[-2:]],
    }


def _bi_summary(bi: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not bi:
        return None
    return {
        "dir": bi["dir"],
        "start_time": bi["start_time"],
        "end_time": bi["end_time"],
        "low": bi["low"], "high": bi["high"],
        "macd_area": round(bi.get("macd", {}).get("macd_area", 0), 3),
        "dif_peak": round(bi.get("macd", {}).get("dif_peak", 0), 4),
        "dea_avg": round(bi.get("macd", {}).get("dea_avg", 0), 4),
        "divergence": bi.get("divergence"),
    }


def _zs_summary(zs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not zs:
        return None
    return {
        "zg": zs["zg"], "zd": zs["zd"],
        "high": zs["high"], "low": zs["low"],
        "bi_count": zs["bi_count"],
        "status": zs["status"],
        "start_time": zs["start_time"],
        "end_time": zs["end_time"],
    }
