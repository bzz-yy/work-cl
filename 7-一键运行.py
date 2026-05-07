from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = BASE_DIR / "输出"
DATA_DIR = OUTPUT_ROOT / "数据获取"
ANALYSIS_DIR = OUTPUT_ROOT / "分析和笔"
INDICATOR_DIR = OUTPUT_ROOT / "指标"
PIVOT_DIR = OUTPUT_ROOT / "中枢"
SIGNAL_DIR = OUTPUT_ROOT / "信号"
REPORT_DIR = OUTPUT_ROOT / "报告"


def normalize_stock_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code[:2] in ("83", "87", "43", "92"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def calc_time_range(lookback_days: int) -> tuple[str, str]:
    today = datetime.now().date()
    end_dt = datetime.combine(today, datetime.strptime("15:15", "%H:%M").time())
    start_dt = datetime.combine(
        today - timedelta(days=lookback_days),
        datetime.strptime("09:15", "%H:%M").time(),
    )
    fmt = "%Y-%m-%d %H:%M:%S"
    return start_dt.strftime(fmt), end_dt.strftime(fmt)


def run_step(step_name: str, command: List[str]) -> None:
    print(f"\n=== {step_name} ===", flush=True)
    print("命令:", " ".join(f'"{x}"' if " " in x else x for x in command), flush=True)
    subprocess.run(command, check=True, cwd=BASE_DIR)


def estimate_ths_indicator_usage() -> dict[str, int]:
    # 1-获取合并.py: low,high
    # 1b-获取指标.py: DIFF, DEA, MACD 各 1 次
    return {
        "api_call_count": 4,
        "indicator_count_min": 5,
        "indicator_count_max": 5,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="缠论 7 步一键运行脚本")
    parser.add_argument("--code", required=True, help="股票代码，例如 600519 或 600519.SH")
    parser.add_argument("--lookback-days", type=int, default=30, help="默认回看天数")
    parser.add_argument("--start-time", default="", help="开始时间 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end-time", default="", help="结束时间 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--min-gap", type=int, default=4, help="第 2 步成笔最小 K 线间隔")
    parser.add_argument("--zone-pct", type=float, default=0.015, help="第 5 步买点区间比例")
    parser.add_argument(
        "--keep-incomplete",
        action="store_true",
        help="第 1 步是否保留未完成的 30min/60min K 线",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    code = normalize_stock_code(args.code)
    start_time, end_time = (
        (args.start_time, args.end_time)
        if args.start_time and args.end_time
        else calc_time_range(args.lookback_days)
    )

    for path in (DATA_DIR, ANALYSIS_DIR, INDICATOR_DIR, PIVOT_DIR, SIGNAL_DIR, REPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)

    stroke_5min = ANALYSIS_DIR / "ths_5min_2_1strokes.csv"
    indicator_5min = INDICATOR_DIR / "ths_5min_indicators.csv"

    usage = estimate_ths_indicator_usage()
    print("本次运行参数", flush=True)
    print(f"- code: {code}", flush=True)
    print(f"- start_time: {start_time}", flush=True)
    print(f"- end_time: {end_time}", flush=True)
    print(f"- 同花顺接口调用次数: {usage['api_call_count']}", flush=True)
    print(
        f"- 同花顺指标计数范围: {usage['indicator_count_min']} ~ {usage['indicator_count_max']}",
        flush=True,
    )

    run_step(
        "第1步 获取合并K线",
        [
            sys.executable,
            str(BASE_DIR / "1-获取合并.py"),
            "--code",
            code,
            "--start-time",
            start_time,
            "--end-time",
            end_time,
            "--output-dir",
            str(DATA_DIR),
            *(["--keep-incomplete"] if args.keep_incomplete else []),
        ],
    )
    run_step(
        "第1b步 获取指标",
        [
            sys.executable,
            str(BASE_DIR / "1b-获取指标.py"),
            "--code",
            code,
            "--interval",
            "5",
            "--start-time",
            start_time,
            "--end-time",
            end_time,
            "--output",
            str(indicator_5min),
        ],
    )
    run_step(
        "第2步 分型和笔",
        [
            sys.executable,
            str(BASE_DIR / "2-分型和笔.py"),
            "--input",
            str(DATA_DIR),
            "--output-dir",
            str(ANALYSIS_DIR),
            "--min-gap",
            str(args.min_gap),
        ],
    )
    run_step(
        "第3步 线段",
        [
            sys.executable,
            str(BASE_DIR / "3-线段.py"),
            "--input",
            str(ANALYSIS_DIR),
            "--output-dir",
            str(ANALYSIS_DIR),
        ],
    )
    run_step(
        "第4步 中枢",
        [
            sys.executable,
            str(BASE_DIR / "4-中枢.py"),
            "--input",
            str(ANALYSIS_DIR),
            "--out-dir",
            str(PIVOT_DIR),
            "--mode",
            "both",
        ],
    )
    run_step(
        "第5步 背驰买点",
        [
            sys.executable,
            str(BASE_DIR / "5-背驰买点.py"),
            "--strokes",
            str(stroke_5min),
            "--indicators",
            str(indicator_5min),
            "--zone-pct",
            str(args.zone_pct),
            "--out-dir",
            str(SIGNAL_DIR),
        ],
    )
    run_step(
        "第6步 生成报告",
        [
            sys.executable,
            str(BASE_DIR / "6-生成报告.py"),
            "--code",
            code,
            "--input-dir",
            str(ANALYSIS_DIR),
            "--pivot-dir",
            str(PIVOT_DIR),
            "--indicator-dir",
            str(INDICATOR_DIR),
            "--signal-dir",
            str(SIGNAL_DIR),
            "--out-dir",
            str(REPORT_DIR),
        ],
    )

    print("\n=== 全部完成 ===", flush=True)
    print(f"报告文件: {REPORT_DIR / f'{code}_report.md'}", flush=True)
    print(f"结构化报告: {REPORT_DIR / f'{code}_report.json'}", flush=True)


if __name__ == "__main__":
    main()
