from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

import pandas as pd

"""
5-背驰买点.py
=============
基于 5min 笔（2-分型和笔.py 输出）与 5min 指标（1b-获取指标.py 输出），做最小可用版背驰识别，
并输出“买点区间（±1.5%）”提示。

口径（你已确认）：
- 5min 为主操作级别
- diff 主判定，macd 柱辅助

注意：这是“可产品化的最小闭环版本”，不强行贴全套 1/2/3 买定义，
先把背驰 + 区间提示跑通，后续再叠加“中枢语境过滤/买卖点分类”。
"""


@dataclass
class Stroke:
    index: int
    direction: str  # up/down
    start_dt: str
    end_dt: str
    high: float
    low: float
    kline_count: int


def load_strokes(p: Path) -> List[Stroke]:
    strokes: List[Stroke] = []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        need = {"index", "direction", "start_dt", "end_dt", "high", "low", "kline_count"}
        if not r.fieldnames:
            raise ValueError(f"{p.name} 缺少表头")
        miss = need - set(r.fieldnames)
        if miss:
            raise ValueError(f"{p.name} 缺字段: {sorted(miss)}")
        for row in r:
            d = (row["direction"] or "").strip().lower()
            if d not in {"up", "down"}:
                continue
            strokes.append(
                Stroke(
                    index=int(row["index"]),
                    direction=d,
                    start_dt=row["start_dt"],
                    end_dt=row["end_dt"],
                    high=float(row["high"]),
                    low=float(row["low"]),
                    kline_count=int(row["kline_count"]),
                )
            )
    strokes.sort(key=lambda s: s.index)
    for i, s in enumerate(strokes):
        s.index = i
    return strokes


def load_indicators(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p, encoding="utf-8-sig")
    cols = {c.lower(): c for c in df.columns}
    need = {"time", "diff", "dea", "macd"}
    missing = need - set(cols.keys())
    if missing:
        raise ValueError(f"{p.name} 缺字段: {sorted(missing)}，实际: {list(df.columns)}")
    df = df.rename(columns={cols[k]: k for k in cols})
    df["time"] = pd.to_datetime(df["time"])
    for c in ("diff", "dea", "macd"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df[["time", "diff", "dea", "macd"]]


def value_at_or_before(df: pd.DataFrame, ts: pd.Timestamp) -> Optional[pd.Series]:
    """
    找到 time <= ts 的最后一行（对齐笔 end_dt 到指标序列）。
    """
    idx = df["time"].searchsorted(ts, side="right") - 1
    if idx < 0:
        return None
    return df.iloc[int(idx)]


@dataclass
class Signal:
    kind: str  # bullish_divergence / bearish_divergence
    stroke_idx_1: int
    stroke_idx_2: int
    dt_1: str
    dt_2: str
    price_1: float
    price_2: float
    diff_1: float
    diff_2: float
    macd_1: float
    macd_2: float
    buy_zone_low: Optional[float] = None
    buy_zone_high: Optional[float] = None


def end_indicator(ind: pd.DataFrame, stroke: Stroke) -> Optional[pd.Series]:
    return value_at_or_before(ind, pd.to_datetime(stroke.end_dt))


def build_signal(s1: Stroke, s2: Stroke, v1: pd.Series, v2: pd.Series, zone_pct: float) -> Optional[Signal]:
    price1_low, price2_low = s1.low, s2.low
    price1_high, price2_high = s1.high, s2.high
    diff1, diff2 = float(v1["diff"]), float(v2["diff"])
    macd1, macd2 = float(v1["macd"]), float(v2["macd"])

    if s1.direction == "down" and s2.direction == "down":
        if price2_low < price1_low and diff2 > diff1 and macd2 >= macd1:
            center = price2_low
            return Signal(
                kind="bullish_divergence",
                stroke_idx_1=s1.index,
                stroke_idx_2=s2.index,
                dt_1=s1.end_dt,
                dt_2=s2.end_dt,
                price_1=price1_low,
                price_2=price2_low,
                diff_1=diff1,
                diff_2=diff2,
                macd_1=macd1,
                macd_2=macd2,
                buy_zone_low=round(center * (1 - zone_pct), 2),
                buy_zone_high=round(center * (1 + zone_pct), 2),
            )
        return None

    if s1.direction == "up" and s2.direction == "up":
        if price2_high > price1_high and diff2 < diff1 and macd2 <= macd1:
            return Signal(
                kind="bearish_divergence",
                stroke_idx_1=s1.index,
                stroke_idx_2=s2.index,
                dt_1=s1.end_dt,
                dt_2=s2.end_dt,
                price_1=price1_high,
                price_2=price2_high,
                diff_1=diff1,
                diff_2=diff2,
                macd_1=macd1,
                macd_2=macd2,
            )
    return None


def detect_divergence_candidates(
    strokes: List[Stroke],
    ind: pd.DataFrame,
    zone_pct: float = 0.015,
) -> List[Signal]:
    """
    扫描全量候选背驰，返回按时间顺序排列的命中列表。
    - 底背驰：两次下跌笔末端，价格创新低但 diff 抬高（macd柱辅助：macd2 >= macd1）
    - 顶背驰：两次上升笔末端，价格创新高但 diff 走低（macd柱辅助：macd2 <= macd1）
    """
    if len(strokes) < 4:
        return []

    down = [s for s in strokes if s.direction == "down"]
    up = [s for s in strokes if s.direction == "up"]
    hits: List[Signal] = []

    for seq in (down, up):
        for idx in range(len(seq) - 1):
            s1, s2 = seq[idx], seq[idx + 1]
            v1, v2 = end_indicator(ind, s1), end_indicator(ind, s2)
            if v1 is None or v2 is None:
                continue
            sig = build_signal(s1, s2, v1, v2, zone_pct)
            if sig is not None:
                hits.append(sig)

    hits.sort(key=lambda s: (s.dt_2, s.stroke_idx_2))
    return hits


def detect_latest_divergence(
    strokes: List[Stroke],
    ind: pd.DataFrame,
    zone_pct: float = 0.015,
) -> Optional[Signal]:
    hits = detect_divergence_candidates(strokes, ind, zone_pct=zone_pct)
    return hits[-1] if hits else None

def save_signals_csv(signals: List[Signal], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "kind",
                "stroke_idx_1",
                "stroke_idx_2",
                "dt_1",
                "dt_2",
                "price_1",
                "price_2",
                "diff_1",
                "diff_2",
                "macd_1",
                "macd_2",
                "buy_zone_low",
                "buy_zone_high",
            ]
        )
        for sig in signals:
            w.writerow(
                [
                    sig.kind,
                    sig.stroke_idx_1,
                    sig.stroke_idx_2,
                    sig.dt_1,
                    sig.dt_2,
                    round(sig.price_1, 2),
                    round(sig.price_2, 2),
                    round(sig.diff_1, 6),
                    round(sig.diff_2, 6),
                    round(sig.macd_1, 6),
                    round(sig.macd_2, 6),
                    "" if sig.buy_zone_low is None else sig.buy_zone_low,
                    "" if sig.buy_zone_high is None else sig.buy_zone_high,
                ]
            )


def save_signal_csv(sig: Signal, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "kind",
                "stroke_idx_1",
                "stroke_idx_2",
                "dt_1",
                "dt_2",
                "price_1",
                "price_2",
                "diff_1",
                "diff_2",
                "macd_1",
                "macd_2",
                "buy_zone_low",
                "buy_zone_high",
            ]
        )
        w.writerow(
            [
                sig.kind,
                sig.stroke_idx_1,
                sig.stroke_idx_2,
                sig.dt_1,
                sig.dt_2,
                round(sig.price_1, 2),
                round(sig.price_2, 2),
                round(sig.diff_1, 6),
                round(sig.diff_2, 6),
                round(sig.macd_1, 6),
                round(sig.macd_2, 6),
                "" if sig.buy_zone_low is None else sig.buy_zone_low,
                "" if sig.buy_zone_high is None else sig.buy_zone_high,
            ]
        )


def save_report_md(sig: Optional[Signal], out: Path, zone_pct: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# 缠论信号简报（最小版）")
    lines.append("")
    if sig is None:
        lines.append("本周期内未检测到满足规则的背驰信号（或数据不足）。")
        out.write_text("\n".join(lines), encoding="utf-8")
        return

    if sig.kind == "bullish_divergence":
        lines.append("## 买点提示：笔底背驰（diff 主判定，macd柱辅助）")
        lines.append("")
        lines.append(f"- 对比下跌笔：#{sig.stroke_idx_1} → #{sig.stroke_idx_2}")
        lines.append(f"- 价格：{sig.price_1:.2f} → {sig.price_2:.2f}（创新低）")
        lines.append(f"- diff：{sig.diff_1:.6f} → {sig.diff_2:.6f}（抬高）")
        lines.append(f"- macd柱：{sig.macd_1:.6f} → {sig.macd_2:.6f}（缩短/走强）")
        lines.append("")
        lines.append(f"建议买点区间（±{zone_pct*100:.1f}%）：**[{sig.buy_zone_low:.2f}, {sig.buy_zone_high:.2f}]**")
        lines.append("")
        lines.append("> 风险提示：若价格有效跌破区间下沿且短期不回，则该背驰条件可能失效，需等待新笔确认后复核。")
    else:
        lines.append("## 风险提示：笔顶背驰（偏卖点信号）")
        lines.append("")
        lines.append(f"- 对比上升笔：#{sig.stroke_idx_1} → #{sig.stroke_idx_2}")
        lines.append(f"- 价格：{sig.price_1:.2f} → {sig.price_2:.2f}（创新高）")
        lines.append(f"- diff：{sig.diff_1:.6f} → {sig.diff_2:.6f}（走低）")
        lines.append(f"- macd柱：{sig.macd_1:.6f} → {sig.macd_2:.6f}（走弱）")

    out.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="第 5 步：背驰识别 + 买点区间（最小版）")
    p.add_argument("--strokes", required=True, help="输入：ths_5min_2_1strokes.csv")
    p.add_argument("--indicators", required=True, help="输入：ths_5min_indicators.csv")
    p.add_argument("--zone-pct", type=float, default=0.015, help="区间百分比（默认 0.015 = ±1.5%）")
    p.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "输出" / "信号"), help="输出目录")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    strokes = load_strokes(Path(args.strokes))
    ind = load_indicators(Path(args.indicators))
    signals = detect_divergence_candidates(strokes, ind, zone_pct=args.zone_pct)
    sig = signals[-1] if signals else None

    out_dir = Path(args.out_dir)
    out_csv = out_dir / "ths_5min_5_signal_latest.csv"
    out_all_csv = out_dir / "ths_5min_5_signals.csv"
    out_md = out_dir / "ths_5min_5_signal_latest.md"
    save_signals_csv(signals, out_all_csv)
    if sig is not None:
        save_signal_csv(sig, out_csv)
    else:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        out_csv.write_text("", encoding="utf-8")
    save_report_md(sig, out_md, args.zone_pct)

    print(f"已输出: {out_csv}")
    print(f"已输出: {out_all_csv}")
    print(f"已输出: {out_md}")


if __name__ == "__main__":
    main()
