from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple


class Direction(Enum):
    UP = 1
    DOWN = -1


@dataclass
class Stroke:
    index: int
    direction: Direction
    start_dt: str
    end_dt: str
    high: float
    low: float
    kline_count: int = 0


@dataclass
class Segment:
    index: int
    direction: Direction
    start_stroke_idx: int
    end_stroke_idx: int
    start_dt: str
    end_dt: str
    high: float
    low: float
    stroke_count: int
    termination: str = "unknown"

    def __repr__(self) -> str:
        direction_label = "UP" if self.direction == Direction.UP else "DOWN"
        return (
            f"Seg#{self.index:>3} {direction_label}  "
            f"{self.start_dt} -> {self.end_dt}  "
            f"H={self.high:.2f}  L={self.low:.2f}  "
            f"({self.stroke_count}笔) [{self.termination}]"
        )


DEFAULT_INPUT_DIR = Path(__file__).resolve().parent / "输出" / "分析和笔"
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR


def has_overlap(s1: Stroke, s2: Stroke, s3: Stroke) -> bool:
    overlap_low = max(s1.low, s2.low, s3.low)
    overlap_high = min(s1.high, s2.high, s3.high)
    return overlap_low <= overlap_high


def process_containment(
    seq: List[list], high: float, low: float, scan_idx: int, seg_dir: Direction
) -> List[list]:
    if not seq:
        return [[high, low, scan_idx]]

    last_high, last_low = seq[-1][0], seq[-1][1]
    is_contained = (high <= last_high and low >= last_low) or (
        last_high <= high and last_low >= low
    )

    if is_contained:
        if seg_dir == Direction.UP:
            new_high, new_low = min(last_high, high), min(last_low, low)
        else:
            new_high, new_low = max(last_high, high), max(last_low, low)
        seq[-1] = [new_high, new_low, max(seq[-1][2], scan_idx)]
    else:
        seq.append([high, low, scan_idx])

    return seq


def build_segments(strokes: List[Stroke]) -> List[Segment]:
    if len(strokes) < 3:
        return []

    segments: List[Segment] = []
    seg_idx = 0
    start = 0
    stroke_total = len(strokes)

    while start + 2 < stroke_total:
        s1, s2, s3 = strokes[start], strokes[start + 1], strokes[start + 2]
        if not has_overlap(s1, s2, s3):
            start += 1
            continue

        direction = s1.direction
        if segments and direction == segments[-1].direction:
            start += 1
            continue

        char_seq: List[list] = []
        char_seq = process_containment(char_seq, s2.high, s2.low, start, direction)
        term_type = "data_end"
        curr_end = start + 2
        pending_case2_end: Optional[int] = None
        confirmed = False

        for scan_idx in range(start + 2, stroke_total, 2):
            curr_end = scan_idx
            if scan_idx + 1 >= stroke_total:
                break

            if pending_case2_end is not None:
                pending_extreme = (
                    strokes[pending_case2_end].high
                    if direction == Direction.UP
                    else strokes[pending_case2_end].low
                )
                curr_extreme = (
                    strokes[scan_idx].high
                    if direction == Direction.UP
                    else strokes[scan_idx].low
                )
                invalidated = (
                    curr_extreme > pending_extreme
                    if direction == Direction.UP
                    else curr_extreme < pending_extreme
                )
                if invalidated:
                    pending_case2_end = None

            next_opp_stroke = strokes[scan_idx + 1]
            char_seq = process_containment(
                char_seq, next_opp_stroke.high, next_opp_stroke.low, scan_idx, direction
            )

            if len(char_seq) < 3:
                continue

            e1, e2, e3 = char_seq[-3], char_seq[-2], char_seq[-1]
            if direction == Direction.UP:
                is_fractal = e2[0] > e1[0] and e2[0] > e3[0]
                has_gap = e2[1] > e1[0]
            else:
                is_fractal = e2[1] < e1[1] and e2[1] < e3[1]
                has_gap = e2[0] < e1[1]

            if not is_fractal:
                continue

            e2_scan_idx = e2[2]
            candidate_indices = range(start + 2, e2_scan_idx + 1, 2)
            if direction == Direction.UP:
                true_end_idx = max(candidate_indices, key=lambda i: strokes[i].high)
            else:
                true_end_idx = min(candidate_indices, key=lambda i: strokes[i].low)

            if has_gap:
                curr_end = true_end_idx
                term_type = "case1"
                confirmed = True
                break

            if pending_case2_end is None:
                pending_case2_end = true_end_idx
                continue

            curr_end = pending_case2_end
            term_type = "case2"
            confirmed = True
            break

        if not confirmed and pending_case2_end is not None:
            curr_end = pending_case2_end
            term_type = "case2"

        end = curr_end
        segments.append(
            Segment(
                index=seg_idx,
                direction=direction,
                start_stroke_idx=start,
                end_stroke_idx=end,
                start_dt=strokes[start].start_dt,
                end_dt=strokes[end].end_dt,
                high=max(stroke.high for stroke in strokes[start : end + 1]),
                low=min(stroke.low for stroke in strokes[start : end + 1]),
                stroke_count=end - start + 1,
                termination=term_type,
            )
        )
        seg_idx += 1
        start = end

    return segments


def validate_strokes(strokes: List[Stroke]) -> bool:
    ok = True
    for idx in range(len(strokes) - 1):
        if strokes[idx].direction == strokes[idx + 1].direction:
            print(
                f"  [WARN] 方向未交替：stroke[{idx}] 与 stroke[{idx + 1}] "
                f"均为 {strokes[idx].direction.name}"
            )
            ok = False
    return ok


def load_strokes(csv_file: Path) -> List[Stroke]:
    required_fields = {
        "index",
        "direction",
        "start_dt",
        "end_dt",
        "high",
        "low",
        "kline_count",
    }
    strokes: List[Stroke] = []

    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        missing = required_fields - fieldnames
        if missing:
            raise ValueError(f"CSV 缺少必要字段: {sorted(missing)}")

        for row_num, row in enumerate(reader, start=2):
            direction_raw = row["direction"].strip().lower()
            if direction_raw not in {"up", "down"}:
                raise ValueError(f"第 {row_num} 行 direction 异常: {row['direction']}")

            strokes.append(
                Stroke(
                    index=int(row["index"]),
                    direction=Direction.UP if direction_raw == "up" else Direction.DOWN,
                    start_dt=row["start_dt"],
                    end_dt=row["end_dt"],
                    high=float(row["high"]),
                    low=float(row["low"]),
                    kline_count=int(row["kline_count"]),
                )
            )

    strokes.sort(key=lambda stroke: stroke.index)
    for idx, stroke in enumerate(strokes):
        stroke.index = idx
    return strokes


def save_segments(segments: List[Segment], output_file: Path) -> None:
    fieldnames = [
        "index",
        "direction",
        "start_stroke_idx",
        "end_stroke_idx",
        "start_dt",
        "end_dt",
        "high",
        "low",
        "stroke_count",
        "termination",
    ]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for segment in segments:
            writer.writerow(
                {
                    "index": segment.index,
                    "direction": "up" if segment.direction == Direction.UP else "down",
                    "start_stroke_idx": segment.start_stroke_idx,
                    "end_stroke_idx": segment.end_stroke_idx,
                    "start_dt": segment.start_dt,
                    "end_dt": segment.end_dt,
                    "high": round(segment.high, 2),
                    "low": round(segment.low, 2),
                    "stroke_count": segment.stroke_count,
                    "termination": segment.termination,
                }
            )


def _infer_period(input_file: Path) -> str:
    name = input_file.stem.lower()
    for period in ("5min", "30min", "60min"):
        if period in name:
            return period
    return "unknown"


def resolve_output(input_file: Path, output_dir: Path) -> Path:
    period = _infer_period(input_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"ths_{period}_3_segments.csv"


def print_segment_stats(segments: List[Segment]) -> None:
    if not segments:
        print("[线段识别] 未找到有效线段")
        return

    counts = [segment.stroke_count for segment in segments]
    up_count = sum(1 for segment in segments if segment.direction == Direction.UP)
    odd_violations = sum(1 for count in counts if count % 2 == 0)
    term_counts = {
        key: sum(1 for segment in segments if segment.termination == key)
        for key in ("case1", "case2", "data_end")
    }

    print(f"[线段识别] 共 {len(segments)} 条 (上升:{up_count}, 下降:{len(segments) - up_count})")
    print(
        f"    终结方式 case1:{term_counts['case1']}  "
        f"case2:{term_counts['case2']}  data_end:{term_counts['data_end']}"
    )
    print(
        f"    笔数分布 min={min(counts)}  max={max(counts)}  "
        f"avg={sum(counts) / len(counts):.1f}"
    )
    if odd_violations:
        print(f"    [WARN] 含偶数笔线段 {odd_violations} 条，请检查")
    else:
        print("    [OK] 所有线段笔数均为奇数")


def collect_input_files(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.glob("*_2_1strokes.csv"))


def analyze_one_file(input_file: Path, output_dir: Path) -> Tuple[List[Stroke], List[Segment], Path]:
    print(f"\n处理文件: {input_file}")
    strokes = load_strokes(input_file)
    print(f"读取笔数据: {len(strokes)} 条")

    if validate_strokes(strokes):
        print("    [OK] 笔方向严格交替")

    segments = build_segments(strokes)
    print_segment_stats(segments)

    output_file = resolve_output(input_file, output_dir)
    save_segments(segments, output_file)
    print(f"已输出线段: {output_file}")
    return strokes, segments, output_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于笔数据计算线段。")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_DIR),
        help="输入文件或目录，默认读取 `2-分型和笔.py` 输出目录中的 *_2_1strokes.csv",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="线段结果输出目录",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    input_files = collect_input_files(input_path)

    if not input_files:
        raise FileNotFoundError(f"未找到待分析的 strokes CSV: {input_path}")

    for input_file in input_files:
        _, segments, _ = analyze_one_file(input_file, output_dir)
        if segments:
            print("=== 全部线段 ===")
            for segment in segments:
                print(f"  {segment}")


if __name__ == "__main__":
    main()
