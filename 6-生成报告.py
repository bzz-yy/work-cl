from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

"""
6-生成报告.py
============
将前面各步骤输出汇总成“可给 AI 读取”的结构化报告：
- report.json（结构化，适合 AI / 前端）
- report.md（人类可读）

强调：
- 明确写出数据时间范围（每个周期分别写：结构数据范围 + 指标数据范围）
- 目前主战场是 5min：报告重点展示 5min 的结构/中枢/背驰买点

输入约定（文件名可带 UUID 前缀）
--------------------------------
结构文件（来自 2/3/4 步）：
- *ths_{period}_2_1strokes.csv
- *ths_{period}_3_segments.csv
- ths_{period}_4_pivot_stroke.csv / ths_{period}_4_pivot_segment.csv（你用 4-中枢.py 生成）

指标与信号（来自 1b/5 步）：
- *ths_{period}_indicators.csv（默认我们生成 ths_5min_indicators.csv）
- ths_5min_5_signal_latest.csv / ths_5min_5_signal_latest.md（最小版背驰买点输出）
"""


PERIODS = ["5min", "30min", "60min"]


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_one(base: Path, pattern: str) -> Optional[Path]:
    matches = sorted(base.glob(pattern))
    return matches[0] if matches else None


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def _parse_dt_series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_datetime(df[col], errors="coerce")


def _range_from_strokes(df: pd.DataFrame) -> Optional[Tuple[str, str]]:
    if df.empty:
        return None
    if "start_dt" not in df.columns or "end_dt" not in df.columns:
        return None
    s = _parse_dt_series(df, "start_dt").dropna()
    e = _parse_dt_series(df, "end_dt").dropna()
    if s.empty or e.empty:
        return None
    return (s.min().strftime("%Y-%m-%d %H:%M:%S"), e.max().strftime("%Y-%m-%d %H:%M:%S"))


def _range_from_time(df: pd.DataFrame, col: str = "time") -> Optional[Tuple[str, str]]:
    if df.empty or col not in df.columns:
        return None
    t = pd.to_datetime(df[col], errors="coerce").dropna()
    if t.empty:
        return None
    return (t.min().strftime("%Y-%m-%d %H:%M:%S"), t.max().strftime("%Y-%m-%d %H:%M:%S"))


def _latest_rows(df: pd.DataFrame, n: int = 5) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    return df.tail(n).to_dict(orient="records")


def _safe_float(x: Any) -> Optional[float]:
    try:
        if pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def _summarize_pivots(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"count": 0, "latest": [], "status_count": {}}
    status_count: Dict[str, int] = {}
    if "status" in df.columns:
        status_count = df["status"].fillna("unknown").value_counts().to_dict()
    cols = [c for c in ["index", "start_dt", "end_dt", "ZG", "ZD", "unit_count", "status", "exit_direction"] if c in df.columns]
    latest = df[cols].tail(5).to_dict(orient="records")
    return {"count": int(len(df)), "latest": latest, "status_count": status_count}


def _summarize_segments(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"count": 0, "latest": []}
    cols = [c for c in ["index", "direction", "start_dt", "end_dt", "high", "low", "stroke_count", "termination"] if c in df.columns]
    latest = df[cols].tail(3).to_dict(orient="records")
    return {"count": int(len(df)), "latest": latest}


def _signal_kind_label(kind: Any) -> str:
    if kind == "bullish_divergence":
        return "底背驰"
    if kind == "bearish_divergence":
        return "顶背驰"
    return str(kind) if kind is not None else ""


def _fmt_signal_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _summarize_structure(period: str, strokes: pd.DataFrame, segments: pd.DataFrame) -> Dict[str, Any]:
    warnings: List[str] = []
    if segments is not None and not segments.empty and "stroke_count" in segments.columns:
        even_bad = int((segments["stroke_count"].astype(str).str.strip() != "").sum())  # best-effort
        # 这里不强行逐行判奇偶（你 3-线段.py 已经做过），报告侧只做信息提示
        _ = even_bad
    if segments is None or segments.empty:
        warnings.append("线段数量不足（<3），无法进行线段级别中枢/走势判断")
    elif len(segments) < 3:
        warnings.append("线段数量偏少（<3），线段中枢结论可靠性较低")

    if strokes is None or strokes.empty:
        warnings.append("笔数据为空，无法进行背驰/中枢等后续分析")

    return {
        "period": period,
        "strokes_count": int(len(strokes)) if strokes is not None else 0,
        "segments_count": int(len(segments)) if segments is not None else 0,
        "structure_range": _range_from_strokes(strokes) if strokes is not None else None,
        "warnings": warnings,
    }


def load_inputs(input_dir: Path, pivot_dir: Path, indicator_dir: Path, signal_dir: Path) -> Dict[str, Dict[str, Optional[Path]]]:
    """
    返回每个 period 的文件路径集合。
    注意：上传文件名可能带 UUID 前缀，所以 pattern 用 *ths_*
    """
    result: Dict[str, Dict[str, Optional[Path]]] = {}
    for period in PERIODS:
        result[period] = {
            "strokes": _find_one(input_dir, f"*ths_{period}_2_1strokes.csv"),
            "segments": _find_one(input_dir, f"*ths_{period}_3_segments.csv"),
            "pivot_stroke": _find_one(pivot_dir, f"ths_{period}_4_pivot_stroke.csv"),
            "pivot_segment": _find_one(pivot_dir, f"ths_{period}_4_pivot_segment.csv"),
            "indicators": _find_one(indicator_dir, f"*ths_{period}_indicators.csv"),
        }
    # 信号目前只做 5min
    result["signals"] = {
        "latest_signal_csv": _find_one(signal_dir, "ths_5min_5_signal_latest.csv"),
        "latest_signal_md": _find_one(signal_dir, "ths_5min_5_signal_latest.md"),
        "signals_csv": _find_one(signal_dir, "ths_5min_5_signals.csv"),
    }
    return result


def build_report(code: str, paths: Dict[str, Dict[str, Optional[Path]]]) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "meta": {
            "stock_code": code,
            "generated_at": _now_str(),
            "note": "本报告为程序自动生成的结构化数据摘要，供 AI 解读；不构成投资建议。",
        },
        "periods": {},
        "signals": {},
    }

    for period in PERIODS:
        p = paths[period]
        strokes_df = _read_csv(p["strokes"]) if p.get("strokes") else pd.DataFrame()
        segments_df = _read_csv(p["segments"]) if p.get("segments") else pd.DataFrame()

        pivot_stroke_df = _read_csv(p["pivot_stroke"]) if p.get("pivot_stroke") else pd.DataFrame()
        pivot_segment_df = _read_csv(p["pivot_segment"]) if p.get("pivot_segment") else pd.DataFrame()

        indicators_df = _read_csv(p["indicators"]) if p.get("indicators") else pd.DataFrame()

        struct = _summarize_structure(period, strokes_df, segments_df)
        ind_range = _range_from_time(indicators_df, "time") if not indicators_df.empty else None

        report["periods"][period] = {
            "files": {
                "strokes": str(p["strokes"]) if p.get("strokes") else None,
                "segments": str(p["segments"]) if p.get("segments") else None,
                "pivot_stroke": str(p["pivot_stroke"]) if p.get("pivot_stroke") else None,
                "pivot_segment": str(p["pivot_segment"]) if p.get("pivot_segment") else None,
                "indicators": str(p["indicators"]) if p.get("indicators") else None,
            },
            "structure": struct,
            "segments": _summarize_segments(segments_df),
            "indicators": {
                "count": int(len(indicators_df)) if not indicators_df.empty else 0,
                "range": ind_range,
                "latest": _latest_rows(indicators_df, n=5) if not indicators_df.empty else [],
            },
            "pivots": {
                "stroke": _summarize_pivots(pivot_stroke_df),
                "segment": _summarize_pivots(pivot_segment_df),
            },
        }

    # signals
    sig_paths = paths["signals"]
    sig_csv = sig_paths.get("latest_signal_csv")
    all_sig_csv = sig_paths.get("signals_csv")
    sig = {}
    all_signals: List[Dict[str, Any]] = []
    candidates_count = 0
    if sig_csv and sig_csv.exists() and sig_csv.stat().st_size > 0:
        sdf = pd.read_csv(sig_csv, encoding="utf-8-sig")
        if not sdf.empty:
            row = sdf.iloc[0].to_dict()
            # 简单做一下类型清洗
            for k in ["price_1", "price_2", "diff_1", "diff_2", "macd_1", "macd_2", "buy_zone_low", "buy_zone_high"]:
                if k in row:
                    row[k] = _safe_float(row[k])
            sig = row
    if all_sig_csv and all_sig_csv.exists() and all_sig_csv.stat().st_size > 0:
        adf = pd.read_csv(all_sig_csv, encoding="utf-8-sig")
        if not adf.empty:
            candidates_count = int(len(adf))
            all_signals = adf.tail(10).to_dict(orient="records")
    report["signals"] = {
        "latest_signal": sig or None,
        "candidates_count": candidates_count,
        "latest_candidates": all_signals,
        "files": {
            "latest_signal_csv": str(sig_paths["latest_signal_csv"]) if sig_paths.get("latest_signal_csv") else None,
            "latest_signal_md": str(sig_paths["latest_signal_md"]) if sig_paths.get("latest_signal_md") else None,
            "signals_csv": str(all_sig_csv) if all_sig_csv else None,
        },
    }
    return report


def render_md(report: Dict[str, Any]) -> str:
    meta = report["meta"]
    lines: List[str] = []
    lines.append(f"# 缠论数据报告：{meta['stock_code']}")
    lines.append("")
    lines.append(f"- 生成时间：{meta['generated_at']}")
    lines.append("")

    # 时间范围汇总表
    lines.append("## 数据时间范围")
    lines.append("")
    lines.append("| 周期 | 结构数据范围（来自笔） | 指标数据范围（diff/dea/macd） |")
    lines.append("|---|---|---|")
    for period in PERIODS:
        p = report["periods"][period]
        srange = p["structure"]["structure_range"]
        irange = p["indicators"]["range"]
        s_txt = f"{srange[0]} ~ {srange[1]}" if srange else "-"
        i_txt = f"{irange[0]} ~ {irange[1]}" if irange else "-"
        lines.append(f"| {period} | {s_txt} | {i_txt} |")
    lines.append("")

    # 5min 重点
    lines.append("## 5min 结构与中枢（重点）")
    p5 = report["periods"]["5min"]
    lines.append(f"- 笔数量：{p5['structure']['strokes_count']}")
    lines.append(f"- 线段数量：{p5['structure']['segments_count']}")
    lines.append(f"- 笔中枢数量：{p5['pivots']['stroke']['count']}")
    lines.append(f"- 线段中枢数量：{p5['pivots']['segment']['count']}")
    if p5["structure"]["warnings"]:
        lines.append("")
        lines.append("**结构提示**：")
        for w in p5["structure"]["warnings"]:
            lines.append(f"- {w}")
    lines.append("")
    if p5["pivots"]["stroke"]["latest"]:
        lines.append("### 最近的笔中枢（最多5个）")
        for x in p5["pivots"]["stroke"]["latest"]:
            lines.append(
                f"- Pivot#{x.get('index')} {x.get('start_dt','')} → {x.get('end_dt','')}"
                f"  ZG={x.get('ZG')}  ZD={x.get('ZD')}  段数={x.get('unit_count')}  状态={x.get('status')}"
            )
        lines.append("")

    # 信号
    lines.append("## 5min 背驰/买点区间（最小版）")
    sig = report.get("signals", {}).get("latest_signal")
    candidate_count = report.get("signals", {}).get("candidates_count", 0)
    lines.append(f"- 候选背驰数量：{candidate_count}")
    if not sig:
        lines.append("- 暂无满足规则的背驰信号（或信号文件未生成）。")
    else:
        kind = sig.get("kind")
        if kind == "bullish_divergence":
            lines.append("- 信号：笔底背驰（diff主判定，macd柱辅助）")
            lines.append(
                f"- 对比下跌笔：#{sig.get('stroke_idx_1')} → #{sig.get('stroke_idx_2')}，时间：{sig.get('dt_1')} → {sig.get('dt_2')}"
            )
            lines.append(f"- 价格（low）：{sig.get('price_1')} → {sig.get('price_2')}（创新低）")
            lines.append(f"- diff：{sig.get('diff_1')} → {sig.get('diff_2')}（抬高）")
            lines.append(f"- macd柱：{sig.get('macd_1')} → {sig.get('macd_2')}（缩短/走强）")
            if sig.get("buy_zone_low") is not None and sig.get("buy_zone_high") is not None:
                lines.append(f"- 建议买点区间（±1.5%）：**[{sig.get('buy_zone_low')}, {sig.get('buy_zone_high')}]**")
        elif kind == "bearish_divergence":
            lines.append("- 信号：笔顶背驰（风险提示/偏卖点）")
            lines.append(
                f"- 对比上升笔：#{sig.get('stroke_idx_1')} → #{sig.get('stroke_idx_2')}，时间：{sig.get('dt_1')} → {sig.get('dt_2')}"
            )
            lines.append(f"- 价格（high）：{sig.get('price_1')} → {sig.get('price_2')}（创新高）")
            lines.append(f"- diff：{sig.get('diff_1')} → {sig.get('diff_2')}（走低）")
            lines.append(f"- macd柱：{sig.get('macd_1')} → {sig.get('macd_2')}（走弱）")
        else:
            lines.append(f"- 信号：{kind}（未识别类型）")
    candidates = report.get("signals", {}).get("latest_candidates", [])
    if candidates:
        recent_candidates = list(reversed(candidates[-3:]))
        lines.append("")
        lines.append("### 最近 3 条候选背驰表")
        lines.append("| 类型 | 笔1 | 笔2 | 结束时间1 | 结束时间2 | 价格1 | 价格2 | diff1 | diff2 | macd1 | macd2 | 买点下沿 | 买点上沿 |")
        lines.append("|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in recent_candidates:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _signal_kind_label(row.get("kind")),
                        _fmt_signal_value(row.get("stroke_idx_1")),
                        _fmt_signal_value(row.get("stroke_idx_2")),
                        _fmt_signal_value(row.get("dt_1")),
                        _fmt_signal_value(row.get("dt_2")),
                        _fmt_signal_value(row.get("price_1")),
                        _fmt_signal_value(row.get("price_2")),
                        _fmt_signal_value(row.get("diff_1")),
                        _fmt_signal_value(row.get("diff_2")),
                        _fmt_signal_value(row.get("macd_1")),
                        _fmt_signal_value(row.get("macd_2")),
                        _fmt_signal_value(row.get("buy_zone_low")),
                        _fmt_signal_value(row.get("buy_zone_high")),
                    ]
                )
                + " |"
            )
    lines.append("")

    # 30/60 简述
    lines.append("## 30/60min（用于背景与过滤）")
    for period in ["30min", "60min"]:
        pp = report["periods"][period]
        lines.append(
            f"- {period}：笔={pp['structure']['strokes_count']}，线段={pp['structure']['segments_count']}，"
            f"笔中枢={pp['pivots']['stroke']['count']}，线段中枢={pp['pivots']['segment']['count']}"
        )
        latest_segment = pp["segments"]["latest"][-1] if pp["segments"]["latest"] else None
        if latest_segment:
            lines.append(
                f"  - 最近线段：#{latest_segment.get('index')} {latest_segment.get('direction')} "
                f"{latest_segment.get('start_dt')} → {latest_segment.get('end_dt')} "
                f"high={latest_segment.get('high')} low={latest_segment.get('low')}"
            )
        latest_pivot_stroke = pp["pivots"]["stroke"]["latest"][-1] if pp["pivots"]["stroke"]["latest"] else None
        if latest_pivot_stroke:
            lines.append(
                f"  - 最近笔中枢：Pivot#{latest_pivot_stroke.get('index')} "
                f"{latest_pivot_stroke.get('start_dt')} → {latest_pivot_stroke.get('end_dt')} "
                f"ZG={latest_pivot_stroke.get('ZG')} ZD={latest_pivot_stroke.get('ZD')} "
                f"状态={latest_pivot_stroke.get('status')}"
            )
        latest_pivot_segment = pp["pivots"]["segment"]["latest"][-1] if pp["pivots"]["segment"]["latest"] else None
        if latest_pivot_segment:
            lines.append(
                f"  - 最近线段中枢：Pivot#{latest_pivot_segment.get('index')} "
                f"{latest_pivot_segment.get('start_dt')} → {latest_pivot_segment.get('end_dt')} "
                f"ZG={latest_pivot_segment.get('ZG')} ZD={latest_pivot_segment.get('ZD')} "
                f"状态={latest_pivot_segment.get('status')}"
            )
        if pp["structure"]["warnings"]:
            for w in pp["structure"]["warnings"]:
                lines.append(f"  - 提示：{w}")
    lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="第6步：生成结构化报告 report.json + report.md")
    p.add_argument("--code", required=True, help="股票代码（仅用于报告展示）")
    p.add_argument(
        "--input-dir",
        default=str(Path(__file__).resolve().parent / "输出" / "分析和笔"),
        help="包含 2/3 步输出或上传 CSV 的目录（默认 实现过程/输出/分析和笔）",
    )
    p.add_argument(
        "--pivot-dir",
        default=str(Path(__file__).resolve().parent / "输出" / "中枢"),
        help="第4步中枢输出目录（默认 实现过程/输出/中枢）",
    )
    p.add_argument(
        "--indicator-dir",
        default=str(Path(__file__).resolve().parent / "输出" / "指标"),
        help="指标输出目录（1b-获取指标.py 输出）",
    )
    p.add_argument(
        "--signal-dir",
        default=str(Path(__file__).resolve().parent / "输出" / "信号"),
        help="信号输出目录（5-背驰买点.py 输出）",
    )
    p.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent / "输出" / "报告"),
        help="报告输出目录",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    pivot_dir = Path(args.pivot_dir).expanduser().resolve()
    indicator_dir = Path(args.indicator_dir).expanduser().resolve()
    signal_dir = Path(args.signal_dir).expanduser().resolve()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = load_inputs(input_dir, pivot_dir, indicator_dir, signal_dir)
    report = build_report(args.code, paths)

    json_path = out_dir / f"{args.code}_report.json"
    md_path = out_dir / f"{args.code}_report.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")

    print(f"已生成: {json_path}")
    print(f"已生成: {md_path}")


if __name__ == "__main__":
    main()
