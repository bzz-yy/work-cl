from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple


class Direction(Enum):
    UP = 1
    DOWN = -1


class FractalType(Enum):
    TOP = 1
    BOTTOM = -1


@dataclass
class KLine:
    dt: str
    high: float
    low: float
    index: int = 0


@dataclass
class Fractal:
    fx_type: FractalType
    klines: List[KLine]
    price: float
    dt: str
    index: int


@dataclass
class Stroke:
    direction: Direction
    start_fractal: Fractal
    end_fractal: Fractal
    high: float
    low: float
    start_dt: str
    end_dt: str
    kline_count: int
    index: int = 0


MIN_GAP = 4
DEFAULT_INPUT_DIR = Path(__file__).resolve().parent / "输出" / "数据获取"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "输出" / "分析和笔"


def load_merged_klines(csv_file: Path) -> List[KLine]:
    klines: List[KLine] = []
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            klines.append(
                KLine(
                    dt=row["time"],
                    high=float(row["high"]),
                    low=float(row["low"]),
                    index=i,
                )
            )
    klines = sorted(klines, key=lambda k: k.dt)
    for i, kline in enumerate(klines):
        kline.index = i
    return klines


def detect_fractals(klines: List[KLine]) -> List[Fractal]:
    if len(klines) < 3:
        return []

    fractals: List[Fractal] = []
    for i in range(1, len(klines) - 1):
        prev_k = klines[i - 1]
        curr_k = klines[i]
        next_k = klines[i + 1]

        if (
            curr_k.high > prev_k.high
            and curr_k.high > next_k.high
            and curr_k.low > prev_k.low
            and curr_k.low > next_k.low
        ):
            fractals.append(
                Fractal(
                    fx_type=FractalType.TOP,
                    klines=[prev_k, curr_k, next_k],
                    price=curr_k.high,
                    dt=curr_k.dt,
                    index=curr_k.index,
                )
            )
        elif (
            curr_k.low < prev_k.low
            and curr_k.low < next_k.low
            and curr_k.high < prev_k.high
            and curr_k.high < next_k.high
        ):
            fractals.append(
                Fractal(
                    fx_type=FractalType.BOTTOM,
                    klines=[prev_k, curr_k, next_k],
                    price=curr_k.low,
                    dt=curr_k.dt,
                    index=curr_k.index,
                )
            )

    top_n = sum(1 for fractal in fractals if fractal.fx_type == FractalType.TOP)
    print(f"[① 分型检测] 疑似分型 {len(fractals)} 个 (顶:{top_n}, 底:{len(fractals) - top_n})")
    return fractals


def _check_fractal_status(
    anchor: Fractal,
    cand: Fractal,
    kline_map: Dict[int, KLine],
    max_idx: int,
) -> str:
    mid_k = cand.klines[1]
    right_k = cand.klines[2]
    search_start = right_k.index + 1

    if anchor.fx_type == FractalType.BOTTOM:
        for idx in range(search_start, max_idx + 1):
            kline = kline_map.get(idx)
            if kline is None:
                continue
            if kline.high > cand.price:
                return "broken"
            if kline.low < mid_k.low:
                return "confirmed"
    else:
        for idx in range(search_start, max_idx + 1):
            kline = kline_map.get(idx)
            if kline is None:
                continue
            if kline.low < cand.price:
                return "broken"
            if kline.high > mid_k.high:
                return "confirmed"

    return "pending"


def build_strokes_stateful(raw_fractals: List[Fractal], klines: List[KLine]) -> List[Stroke]:
    if len(raw_fractals) < 2:
        return []

    kline_map: Dict[int, KLine] = {kline.index: kline for kline in klines}
    max_idx = max(kline_map.keys()) if kline_map else 0

    strokes: List[Stroke] = []
    stroke_idx = 0
    anchor = raw_fractals[0]

    j = 1
    while j < len(raw_fractals):
        cand = raw_fractals[j]

        if cand.fx_type == anchor.fx_type:
            if strokes and anchor is strokes[-1].end_fractal:
                is_more_extreme = (
                    (anchor.fx_type == FractalType.TOP and cand.price > anchor.price)
                    or (anchor.fx_type == FractalType.BOTTOM and cand.price < anchor.price)
                )
                if is_more_extreme:
                    removed = strokes.pop()
                    stroke_idx -= 1
                    anchor = removed.start_fractal
                    continue
            else:
                if anchor.fx_type == FractalType.TOP:
                    if cand.price > anchor.price:
                        anchor = cand
                else:
                    if cand.price < anchor.price:
                        anchor = cand

            j += 1
            continue

        gap = cand.index - anchor.index
        if gap < MIN_GAP:
            j += 1
            continue

        if anchor.fx_type == FractalType.BOTTOM:
            if cand.price <= anchor.price:
                j += 1
                continue
        else:
            if cand.price >= anchor.price:
                j += 1
                continue

        status = _check_fractal_status(anchor, cand, kline_map, max_idx)
        if status == "confirmed":
            direction = Direction.UP if anchor.fx_type == FractalType.BOTTOM else Direction.DOWN
            high = cand.price if direction == Direction.UP else anchor.price
            low = anchor.price if direction == Direction.UP else cand.price
            strokes.append(
                Stroke(
                    direction=direction,
                    start_fractal=anchor,
                    end_fractal=cand,
                    high=high,
                    low=low,
                    start_dt=anchor.dt,
                    end_dt=cand.dt,
                    kline_count=gap + 1,
                    index=stroke_idx,
                )
            )
            stroke_idx += 1
            anchor = cand

        j += 1

    up_n = sum(1 for stroke in strokes if stroke.direction == Direction.UP)
    print(f"[② 笔构建] 有效笔 {len(strokes)} 条 (上升:{up_n}, 下降:{len(strokes) - up_n})")
    return strokes


def validate_strokes(strokes: List[Stroke]) -> List[Stroke]:
    if len(strokes) < 2:
        return strokes

    validated = [strokes[0]]
    triggered = False

    for curr in strokes[1:]:
        last = validated[-1]
        if curr.direction != last.direction:
            validated.append(curr)
        else:
            triggered = True
            if curr.direction == Direction.UP:
                if curr.high > last.high:
                    validated[-1] = curr
            else:
                if curr.low < last.low:
                    validated[-1] = curr

    if triggered:
        print(f"[③ 笔验证] 发现同向相邻笔，已修正 -> {len(validated)} 条")
    else:
        print(f"[③ 笔验证] 方向交替正常，共 {len(validated)} 条")

    for i, stroke in enumerate(validated):
        stroke.index = i
    return validated


def extract_fractals_from_strokes(strokes: List[Stroke]) -> List[Fractal]:
    if not strokes:
        return []

    fractals: List[Fractal] = []
    for stroke in strokes:
        if not fractals:
            fractals.append(stroke.start_fractal)
        fractals.append(stroke.end_fractal)
    return fractals


def detect_strokes(klines: List[KLine], min_gap: int = MIN_GAP) -> Tuple[List[Fractal], List[Stroke]]:
    global MIN_GAP
    MIN_GAP = min_gap

    klines = sorted(klines, key=lambda k: k.dt)
    for i, kline in enumerate(klines):
        kline.index = i

    raw_fractals = detect_fractals(klines)
    raw_strokes = build_strokes_stateful(raw_fractals, klines)
    final_strokes = validate_strokes(raw_strokes)
    valid_fractals = extract_fractals_from_strokes(final_strokes)
    print(f"    有效分型 {len(valid_fractals)} 个")
    return valid_fractals, final_strokes


def _infer_period(input_file: Path) -> str:
    name = input_file.stem.lower()
    for period in ("5min", "30min", "60min"):
        if period in name:
            return period
    return "unknown"


def resolve_outputs(input_file: Path, output_dir: Path) -> Tuple[Path, Path]:
    period = _infer_period(input_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    fractal_output = output_dir / f"ths_{period}_2_2valid_fractals.csv"
    stroke_output = output_dir / f"ths_{period}_2_1strokes.csv"
    return fractal_output, stroke_output


def save_fractals(fractals: List[Fractal], output_file: Path) -> None:
    with output_file.open("w", encoding="utf-8-sig", newline="") as f_csv:
        writer = csv.writer(f_csv)
        writer.writerow(["index", "type", "dt", "price", "k1_index", "k2_index", "k3_index"])
        for fractal in fractals:
            writer.writerow(
                [
                    fractal.index,
                    "top" if fractal.fx_type == FractalType.TOP else "bottom",
                    fractal.dt,
                    fractal.price,
                    fractal.klines[0].index,
                    fractal.klines[1].index,
                    fractal.klines[2].index,
                ]
            )


def save_strokes(strokes: List[Stroke], output_file: Path) -> None:
    with output_file.open("w", encoding="utf-8-sig", newline="") as s_csv:
        writer = csv.writer(s_csv)
        writer.writerow(
            [
                "index",
                "direction",
                "start_dt",
                "end_dt",
                "start_fractal_index",
                "end_fractal_index",
                "high",
                "low",
                "kline_count",
            ]
        )
        for stroke in strokes:
            writer.writerow(
                [
                    stroke.index,
                    "up" if stroke.direction == Direction.UP else "down",
                    stroke.start_dt,
                    stroke.end_dt,
                    stroke.start_fractal.index,
                    stroke.end_fractal.index,
                    stroke.high,
                    stroke.low,
                    stroke.kline_count,
                ]
            )


def analyze_one_file(input_file: Path, output_dir: Path, min_gap: int) -> None:
    print(f"\n处理文件: {input_file}")
    klines = load_merged_klines(input_file)
    print(f"读取合并K线: {len(klines)} 根")
    valid_fractals, strokes = detect_strokes(klines, min_gap=min_gap)

    fractal_output, stroke_output = resolve_outputs(input_file, output_dir)
    save_fractals(valid_fractals, fractal_output)
    save_strokes(strokes, stroke_output)

    print(f"已输出分型: {fractal_output}")
    print(f"已输出笔: {stroke_output}")


def collect_input_files(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.glob("*_merged.csv"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于合并后的K线数据，计算分型和笔。")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_DIR),
        help="输入文件或目录，默认读取 `1-获取合并.py` 输出目录中的 *_merged.csv",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="分型和笔结果输出目录",
    )
    parser.add_argument(
        "--min-gap",
        type=int,
        default=MIN_GAP,
        help="成笔最小K线 index 差，默认 4",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    input_files = collect_input_files(input_path)

    if not input_files:
        raise FileNotFoundError(f"未找到待分析的 merged CSV: {input_path}")

    for input_file in input_files:
        analyze_one_file(input_file, output_dir, min_gap=args.min_gap)


if __name__ == "__main__":
    main()
