"""
narrative.py
============
从分级别结果生成「事实清单」+「兜底文字解读」。

facts 是给上层 LLM 用的结构化事实原子（字符串数组，每条一句话、可独立成立）。
narrative_fallback 是规则拼接的人话，未命中的情况会自动跳过对应句子。
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional


LEVEL_LABEL = {5: "5分钟", 15: "15分钟", 60: "60分钟"}


def build_facts(symbol: str, by_level: Dict[int, Dict[str, Any]]) -> List[str]:
    facts: List[str] = []
    facts.append(f"标的：{symbol}")
    for lv in sorted(by_level.keys(), reverse=True):
        r = by_level[lv]
        lbl = LEVEL_LABEL.get(lv, f"{lv}分钟")
        if r.get("error"):
            facts.append(f"[{lbl}] 数据不足：{r.get('error')}（共 {r.get('bar_count', 0)} 根K）。")
            continue
        facts.append(
            f"[{lbl}] 共 {r['bar_count']} 根K，识别出 {r['bi_count']} 笔、"
            f"{r['zhongshu_count']} 个笔中枢，结构置信度 {r['confidence']}。"
        )
        facts.append(f"[{lbl}] 当前价 {r['last_close']}，整体趋势判定：{r['trend']}。")

        lb = r.get("last_bi")
        if lb:
            facts.append(
                f"[{lbl}] 最近一笔方向 {lb['dir']}，"
                f"从 {lb['start_time']}({lb['low'] if lb['dir']=='up' else lb['high']}) "
                f"到 {lb['end_time']}({lb['high'] if lb['dir']=='up' else lb['low']})。"
            )
            facts.append(
                f"[{lbl}] 最近一笔 MACD 力度：柱面积 {lb['macd_area']}，"
                f"DIF 峰值 {lb['dif_peak']}，DEA 均值 {lb['dea_avg']}"
                f"（{'0轴上方' if lb['dea_avg']>0 else '0轴下方'}）。"
            )
            if lb.get("divergence"):
                d = lb["divergence"]
                facts.append(
                    f"[{lbl}] 最近一笔出现 {d['kind']}，"
                    f"力度比 {d['area_ratio']}，位于 {d['dea_zone']}。"
                )

        lz = r.get("last_zhongshu")
        if lz:
            facts.append(
                f"[{lbl}] 最近笔中枢区间 [{lz['zd']}, {lz['zg']}]，"
                f"扩展范围 [{lz['low']}, {lz['high']}]，含 {lz['bi_count']} 笔，"
                f"状态：{lz['status']}。"
            )
            pos = r.get("position_vs_zs")
            if pos:
                pos_map = {"above": "中枢上方", "below": "中枢下方", "inside": "中枢内部"}
                facts.append(f"[{lbl}] 当前价位于{pos_map.get(pos, pos)}。")
        else:
            facts.append(f"[{lbl}] 暂未形成笔中枢（笔数 {r['bi_count']}）。")

        rt = r.get("realtime_alert")
        if rt and rt.get("potential_impact"):
            for s in rt["potential_impact"]:
                facts.append(f"[{lbl}-实时] {s['scenario']} → {s['result']}；建议：{s['next_action']}。")
    return facts


def build_narrative_fallback(symbol: str, by_level: Dict[int, Dict[str, Any]],
                             as_of: str) -> str:
    """规则拼接，只输出真正命中的句子，未命中的自动跳过。"""
    parts: List[str] = [f"【{symbol} | {as_of}】"]

    # 大方向：60min
    r60 = by_level.get(60)
    if r60 and not r60.get("error"):
        seg = ["", "【大方向（60分钟）】"]
        seg.append(f"整体趋势：{r60['trend']}，置信度 {r60['confidence']}。")
        lb = r60.get("last_bi")
        if lb:
            seg.append(f"最近一笔向{'上' if lb['dir']=='up' else '下'}，"
                       f"区间 {lb['low']}~{lb['high']}。")
            if lb.get("divergence"):
                d = lb["divergence"]
                seg.append(f"该笔出现{'顶' if d['kind']=='top_divergence' else '底'}背驰"
                           f"（力度比 {d['area_ratio']}，{d['dea_zone']}）。")
        lz = r60.get("last_zhongshu")
        if lz:
            seg.append(f"最近中枢 [{lz['zd']}, {lz['zg']}]，状态 {lz['status']}，"
                       f"当前价位于{_pos_cn(r60.get('position_vs_zs'))}。")
        else:
            seg.append("尚未形成段中枢/笔中枢，仅作方向参考。")
        parts.append(" ".join(seg))

    # 中级结构：15min
    r15 = by_level.get(15)
    if r15 and not r15.get("error"):
        seg = ["", "【中级结构（15分钟）】"]
        seg.append(f"趋势 {r15['trend']}，置信度 {r15['confidence']}。")
        lz = r15.get("last_zhongshu")
        if lz:
            seg.append(f"中枢 [{lz['zd']}, {lz['zg']}]（{lz['status']}），"
                       f"当前价{_pos_cn(r15.get('position_vs_zs'))}。")
        lb = r15.get("last_bi")
        if lb and lb.get("divergence"):
            d = lb["divergence"]
            seg.append(f"最近一笔出现{'顶' if d['kind']=='top_divergence' else '底'}背驰。")
        parts.append(" ".join(seg))

    # 小级别触发：5min
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

    # 综合信号
    signals = collect_signals(by_level)
    if signals:
        parts.append("")
        parts.append("【综合信号】")
        for sg in signals:
            parts.append(f"· {sg}")
    else:
        parts.append("")
        parts.append("【综合信号】当前未匹配明确买卖点，建议观望。")

    # 观察清单
    watch = build_watchlist(by_level)
    if watch:
        parts.append("")
        parts.append("【接下来重点观察】")
        for w in watch:
            parts.append(f"· {w}")

    return "\n".join(parts)


def _pos_cn(p: Optional[str]) -> str:
    return {"above": "中枢上方", "below": "中枢下方", "inside": "中枢内部"}.get(p or "", "未知")


def collect_signals(by_level: Dict[int, Dict[str, Any]]) -> List[str]:
    """匹配经典多级别联动信号。"""
    out: List[str] = []
    r60, r15, r5 = by_level.get(60), by_level.get(15), by_level.get(5)

    # 一买：60min 底背驰 + 15min 底分型/底背驰
    if r60 and not r60.get("error"):
        lb60 = r60.get("last_bi") or {}
        d60 = lb60.get("divergence") or {}
        if d60.get("kind") == "bot_divergence":
            out.append("60分钟出现底背驰，存在潜在一类买点；建议结合 15min/5min 寻找精确进场。")
        if d60.get("kind") == "top_divergence":
            out.append("60分钟出现顶背驰，存在潜在一类卖点；注意减仓或离场。")

    # 三买：15min 中枢上破 + 当前价在中枢上方
    if r15 and not r15.get("error"):
        lz15 = r15.get("last_zhongshu")
        pos15 = r15.get("position_vs_zs")
        if lz15 and pos15 == "above" and lz15.get("status") in ("formed", "extending"):
            out.append(f"15分钟当前价位于中枢[{lz15['zd']},{lz15['zg']}]上方，若回踩不破上沿可视为三类买点。")
        if lz15 and pos15 == "below" and lz15.get("status") in ("formed", "extending"):
            out.append(f"15分钟当前价位于中枢[{lz15['zd']},{lz15['zg']}]下方，若反抽不过下沿可视为三类卖点。")

    # 5min 背驰
    if r5 and not r5.get("error"):
        lb5 = r5.get("last_bi") or {}
        d5 = lb5.get("divergence") or {}
        if d5.get("kind") == "bot_divergence":
            out.append("5分钟出现底背驰，短线进场窗口；止损放笔的最低点。")
        if d5.get("kind") == "top_divergence":
            out.append("5分钟出现顶背驰，短线减仓窗口。")

    return out


def build_watchlist(by_level: Dict[int, Dict[str, Any]]) -> List[str]:
    """生成观察清单：关键价位 + 触发条件。"""
    out: List[str] = []
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
            out.append(f"60分钟最近一笔极值 {lb['high'] if lb['dir']=='up' else lb['low']}，关注是否被突破/破坏。")
    return out
