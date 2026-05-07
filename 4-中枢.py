#!/usr/bin/env python3
"""
4-中枢.py
=========
基于“笔(Stroke)”或“线段(Segment)”序列识别中枢（Pivot）。

本脚本定位
----------
- 你的流水线：1-获取合并 -> 2-分型和笔 -> 3-线段
- 本脚本（第 4 步）读取第 2/3 步输出的 CSV：
  - 笔文件（2 输出）：*_2_1strokes.csv
  - 线段文件（3 输出）：*_3_segments.csv
并分别输出：
  - ths_{period}_4_pivot_stroke.csv
  - ths_{period}_4_pivot_segment.csv

实现策略（相对 4_pivot.py 的改进点）
----------------------------------
1) “离开中枢”的判定用 **完全离开**：
   - 向上离开：unit.low > ZG
   - 向下离开：unit.high < ZD
   而不是仅 high>ZG / low<ZD（否则仍可能与中枢区间有交集）。
2) 保留 4_pivot.py 的“离开 -> 回抽 -> 继续扩展”的框架：
   - 若离开后下一单元仍未回到中枢（仍完全在外），则中枢“走完 complete”
   - 若回到中枢（与 [ZD,ZG] 有交集），则继续扩展（end += 2）
3) 默认 max_units=9（奇数），达到后标记 level_up。

注意
----
缠论中“中枢区间 ZG/ZD 是否随扩展动态收缩”在不同实现中有差异。
这里延续了你参考脚本 4_pivot.py 的口径：ZG/ZD 取“最初 3 单元交集”，
后续扩展不再改变 ZG/ZD，只用于判定“离开/回抽/走完”。
如果你希望 ZG/ZD 对扩展单元做“全体交集”动态收缩，我也可以再给你一个可切换版本。
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "输出" / "中枢"


class Direction(Enum):
    UP = 1
    DOWN = -1


@dataclass
class Unit:
    """统一抽象：笔或线段。"""

    index: int  # 文件内顺序索引（从 0 开始重排）
    direction: Direction
    start_dt: str
    end_dt: str
    high: float
    low: float


@dataclass
class Pivot:
    index: int
    ZG: float
    ZD: float
    high: float
    low: float
    start_unit_idx: int
    end_unit_idx: int
    exit_unit_idx: Optional[int]
    return_unit_idx: Optional[int]
    start_dt: str
    end_dt: str
    unit_count: int
    status: str  # active / walked_out / complete / level_up
    exit_direction: Optional[Direction]
    first_unit_direction: Direction

    def __repr__(self) -> str:
        d = "↑" if self.first_unit_direction == Direction.UP else "↓"
        S = {
            "active": "进行中",
            "walked_out": "已走出",
            "complete": "走完  ",
            "level_up": "级别升级",
        }
        ex = (
            f" → 出口{'↑' if self.exit_direction == Direction.UP else '↓'}"
            if self.exit_direction
            else ""
        )
        return (
            f"Pivot#{self.index:>3} {d}  "
            f"{self.start_dt[:16]} → {self.end_dt[:16]}  "
            f"ZG={self.ZG:>8.2f}  ZD={self.ZD:>8.2f}  "
            f"({self.unit_count}段)  [{S.get(self.status, '?')}{ex}]"
        )


def _overlap_exists(a: Unit, b: Unit, c: Unit) -> Tuple[bool, float, float]:
    """返回：是否存在三段交集，以及交集上/下界 (ZG, ZD)。"""
    ZG = min(a.high, b.high, c.high)
    ZD = max(a.low, b.low, c.low)
    return (ZG > ZD), ZG, ZD


def _is_fully_above(u: Unit, ZG: float) -> bool:
    """完全在中枢上方（不与 [ZD,ZG] 相交）。"""
    return u.low > ZG


def _is_fully_below(u: Unit, ZD: float) -> bool:
    """完全在中枢下方（不与 [ZD,ZG] 相交）。"""
    return u.high < ZD


def validate_units(units: List[Unit]) -> bool:
    ok = True
    for j in range(len(units) - 1):
        if units[j].direction == units[j + 1].direction:
            print(
                f"  [WARN] 方向未交替：unit[{j}] 与 unit[{j+1}] 均为 {units[j].direction.name}"
            )
            ok = False
    return ok


def build_pivots(units: List[Unit], max_units: int = 9) -> List[Pivot]:
    if max_units % 2 == 0:
        raise ValueError(f"max_units 必须为奇数，当前: {max_units}")

    n = len(units)
    pivots: List[Pivot] = []
    pivot_idx = 0
    i = 0

    while i + 2 < n:
        u0, u1, u2 = units[i], units[i + 1], units[i + 2]

        # 最小候选要求方向交替，否则滑窗右移。
        if u0.direction == u1.direction or u1.direction == u2.direction:
            i += 1
            continue

        ok, ZG, ZD = _overlap_exists(u0, u1, u2)
        if not ok:
            i += 1
            continue

        end = i + 2
        p_high = max(u0.high, u1.high, u2.high)
        p_low = min(u0.low, u1.low, u2.low)
        status: str = "active"
        exit_dir: Optional[Direction] = None
        exit_unit_idx: Optional[int] = None
        return_unit_idx: Optional[int] = None

        # 继续扩展：每次尝试加入（同向 + 反向）两个单元
        while end + 1 < n:
            u_out = units[end + 1]

            # 是否“完全离开”中枢
            if _is_fully_above(u_out, ZG):
                out_dir = Direction.UP
            elif _is_fully_below(u_out, ZD):
                out_dir = Direction.DOWN
            else:
                out_dir = None

            if out_dir is not None:
                # 离开后没有下一单元可确认
                if end + 2 >= n:
                    status = "walked_out"
                    exit_dir = out_dir
                    exit_unit_idx = end + 1
                    break

                u_ret = units[end + 2]

                # 下一单元仍“完全在外” => 走完
                if out_dir == Direction.UP:
                    still_out = _is_fully_above(u_ret, ZG)
                else:
                    still_out = _is_fully_below(u_ret, ZD)

                if still_out:
                    status = "complete"
                    exit_dir = out_dir
                    exit_unit_idx = end + 1
                    return_unit_idx = end + 2
                    break

                # 回到中枢（与中枢区间相交）=> 继续扩展
                end += 2
                p_high = max(p_high, units[end - 1].high, units[end].high)
                p_low = min(p_low, units[end - 1].low, units[end].low)

                if (end - i + 1) >= max_units:
                    status = "level_up"
                    break
                continue

            # 未离开，尝试正常扩展 2 个单元（保持“奇数段”）
            if end + 2 >= n:
                break
            end += 2
            p_high = max(p_high, units[end - 1].high, units[end].high)
            p_low = min(p_low, units[end - 1].low, units[end].low)
            if (end - i + 1) >= max_units:
                status = "level_up"
                break

        pivots.append(
            Pivot(
                index=pivot_idx,
                ZG=ZG,
                ZD=ZD,
                high=p_high,
                low=p_low,
                start_unit_idx=i,
                end_unit_idx=end,
                exit_unit_idx=exit_unit_idx,
                return_unit_idx=return_unit_idx,
                start_dt=units[i].start_dt,
                end_dt=units[end].end_dt,
                unit_count=end - i + 1,
                status=status,
                exit_direction=exit_dir,
                first_unit_direction=units[i].direction,
            )
        )
        pivot_idx += 1

        # 与 4_pivot.py 一致：避免重叠识别
        i = end + 1

    _print_stats(pivots)
    return pivots


def _print_stats(pivots: List[Pivot]) -> None:
    if not pivots:
        print("[中枢识别] 未找到有效中枢")
        return
    total = len(pivots)
    by_st = {s: sum(1 for p in pivots if p.status == s) for s in ("complete", "active", "walked_out", "level_up")}
    eu = sum(1 for p in pivots if p.exit_direction == Direction.UP)
    ed = sum(1 for p in pivots if p.exit_direction == Direction.DOWN)
    counts = [p.unit_count for p in pivots]
    print(f"\n[中枢识别] 共 {total} 个中枢")
    print(
        f"  状态分布  走完:{by_st['complete']}  已走出:{by_st['walked_out']}  "
        f"进行中:{by_st['active']}  级别升级:{by_st['level_up']}"
    )
    print(f"  出口方向  上方↑:{eu}  下方↓:{ed}")
    print(f"  段数分布  min={min(counts)}  max={max(counts)}  avg={sum(counts) / len(counts):.1f}")
    odd_bad = sum(1 for c in counts if c % 2 == 0)
    if odd_bad:
        print(f"  [WARN] 偶数段中枢: {odd_bad} 个（理论应为奇数）")
    else:
        print("  [OK] 所有中枢段数均为奇数")


def _infer_period_from_name(p: Path) -> str:
    m = re.search(r"(\d+min)", p.name, re.IGNORECASE)
    return m.group(1).lower() if m else "unknown"


def _load_units_generic(csv_path: Path) -> List[Unit]:
    """
    兼容两种输入：
    - strokes:  index,direction,start_dt,end_dt,high,low,kline_count
    - segments: index,direction,start_stroke_idx,end_stroke_idx,start_dt,end_dt,high,low,stroke_count,termination
    """
    required = {"index", "direction", "start_dt", "end_dt", "high", "low"}
    units: List[Unit] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        missing = required - fields
        if missing:
            raise ValueError(f"CSV 缺少必要字段 {sorted(missing)}: {csv_path}")

        for row_num, row in enumerate(reader, start=2):
            d = (row.get("direction") or "").strip().lower()
            if d not in {"up", "down"}:
                print(f"  [WARN] 第 {row_num} 行 direction 异常: {row.get('direction')}, 已跳过")
                continue
            try:
                high = float(row["high"])
                low = float(row["low"])
            except Exception:
                print(f"  [WARN] 第 {row_num} 行 high/low 解析失败，已跳过")
                continue
            units.append(
                Unit(
                    index=0,  # 后面按时间序重排
                    direction=Direction.UP if d == "up" else Direction.DOWN,
                    start_dt=row["start_dt"],
                    end_dt=row["end_dt"],
                    high=high,
                    low=low,
                )
            )

    # 按开始时间排序更稳妥（原文件通常已是顺序）
    units.sort(key=lambda u: (u.start_dt, u.end_dt))
    for i, u in enumerate(units):
        u.index = i
    return units


def save_pivots_to_csv(pivots: List[Pivot], out_path: Path) -> None:
    fields = [
        "index",
        "ZG",
        "ZD",
        "high",
        "low",
        "start_unit_idx",
        "end_unit_idx",
        "exit_unit_idx",
        "return_unit_idx",
        "start_dt",
        "end_dt",
        "unit_count",
        "status",
        "exit_direction",
        "first_unit_direction",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for p in pivots:
            w.writerow(
                {
                    "index": p.index,
                    "ZG": round(p.ZG, 2),
                    "ZD": round(p.ZD, 2),
                    "high": round(p.high, 2),
                    "low": round(p.low, 2),
                    "start_unit_idx": p.start_unit_idx,
                    "end_unit_idx": p.end_unit_idx,
                    "exit_unit_idx": "" if p.exit_unit_idx is None else p.exit_unit_idx,
                    "return_unit_idx": "" if p.return_unit_idx is None else p.return_unit_idx,
                    "start_dt": p.start_dt,
                    "end_dt": p.end_dt,
                    "unit_count": p.unit_count,
                    "status": p.status,
                    "exit_direction": (
                        "up"
                        if p.exit_direction == Direction.UP
                        else ("down" if p.exit_direction == Direction.DOWN else "")
                    ),
                    "first_unit_direction": "up" if p.first_unit_direction == Direction.UP else "down",
                }
            )


def _find_all(pattern: str, base: Path) -> List[Path]:
    return sorted(base.glob(pattern))


def resolve_inputs(input_path: Path) -> Tuple[List[Path], List[Path]]:
    """
    返回：(strokes_csv_list, segments_csv_list)
    - 若 input_path 为文件：根据文件名判断是 strokes 还是 segments
    - 若 input_path 为目录：自动在目录内寻找 *_2_1strokes.csv / *_3_segments.csv
    """
    if input_path.is_file():
        name = input_path.name.lower()
        if name.endswith("_3_segments.csv") or "segments" in name:
            return [], [input_path]
        return [input_path], []

    strokes = _find_all("*_2_1strokes.csv", input_path)
    segments = _find_all("*_3_segments.csv", input_path)
    return strokes, segments


def run_one(csv_file: Path, out_dir: Path, max_units: int, mode: str) -> Path:
    period = _infer_period_from_name(csv_file)
    out_path = out_dir / f"ths_{period}_4_pivot_{mode}.csv"
    print(f"\n[{mode}] 输入: {csv_file}")
    units = _load_units_generic(csv_file)
    print(f"[{mode}] 单元数量: {len(units)}")
    print(f"[{mode}] 校验方向交替...")
    validate_units(units)
    pivots = build_pivots(units, max_units=max_units)
    print(f"\n[{mode}] 最近 10 个中枢：")
    for p in pivots[-10:]:
        print(f"  {p}")
    save_pivots_to_csv(pivots, out_path)
    print(f"[{mode}] 已保存: {out_path}")
    return out_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="第 4 步：中枢识别（笔/线段）")
    p.add_argument(
        "--input",
        required=True,
        help="输入文件或目录。给目录时会自动找 *_2_1strokes.csv 与 *_3_segments.csv",
    )
    p.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="输出目录，默认输出到 实现过程/输出/中枢",
    )
    p.add_argument("--max-units", type=int, default=9, help="单个中枢最多包含的单元数（必须奇数）")
    p.add_argument(
        "--mode",
        default="both",
        choices=["both", "stroke", "segment"],
        help="只跑笔/线段/都跑",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"找不到输入路径: {input_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    strokes_csv_list, segments_csv_list = resolve_inputs(input_path)

    outputs: List[Path] = []
    if args.mode in {"both", "stroke"}:
        if not strokes_csv_list:
            print("[stroke] 未找到笔文件（*_2_1strokes.csv），已跳过")
        else:
            for strokes_csv in strokes_csv_list:
                outputs.append(run_one(strokes_csv, out_dir, args.max_units, mode="stroke"))

    if args.mode in {"both", "segment"}:
        if not segments_csv_list:
            print("[segment] 未找到线段文件（*_3_segments.csv），已跳过")
        else:
            for segments_csv in segments_csv_list:
                outputs.append(run_one(segments_csv, out_dir, args.max_units, mode="segment"))

    if not outputs:
        print("未生成任何输出，请检查 --input 或 --mode。")


if __name__ == "__main__":
    main()
