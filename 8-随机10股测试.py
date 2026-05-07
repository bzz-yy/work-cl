from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Tuple

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
STOCK_SOURCE = BASE_DIR.parent / "技术方案" / "获取股票代码" / "stock_search.py"
TEST_ROOT = BASE_DIR / "测试"

WORKSPACE_DIRNAME = "_工作区"
STEP_DIRS = {
    "step1": "1-获取合并",
    "step1b": "1b-获取指标",
    "step2": "2-分型和笔",
    "step3": "3-线段",
    "step4": "4-中枢",
    "step5": "5-背驰买点",
    "step6": "6-生成报告",
}


@dataclass
class StockItem:
    code: str
    name: str


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


def safe_name(text: str) -> str:
    text = re.sub(r"\s+", "", text.strip())
    text = re.sub(r'[\\/:*?"<>|]', "_", text)
    return text or "未命名股票"


def load_stock_pool() -> List[StockItem]:
    spec = importlib.util.spec_from_file_location("stock_search_module", STOCK_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载股票列表文件: {STOCK_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw_data = getattr(module, "STOCK_DATA", [])
    stocks = [StockItem(code=str(code), name=str(name)) for code, name in raw_data]
    # 为了提高 10 只样本的可跑通率，默认排除 ST/*ST
    return [s for s in stocks if "ST" not in s.name.upper()]


def pick_random_stocks(sample_size: int, seed: int) -> List[StockItem]:
    pool = load_stock_pool()
    if sample_size > len(pool):
        raise ValueError(f"样本数 {sample_size} 超过可用股票池 {len(pool)}")
    rng = random.Random(seed)
    return rng.sample(pool, sample_size)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def reset_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def copy_files(files: Iterable[Path], target_dir: Path) -> None:
    ensure_dir(target_dir)
    for file in files:
        if file.exists() and file.is_file():
            shutil.copy2(file, target_dir / file.name)


def run_command(command: List[str], cwd: Path, log_file: Path) -> None:
    rendered = " ".join(f'"{part}"' if " " in part else part for part in command)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"\n$ {rendered}\n")
        proc = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.stdout:
            f.write(proc.stdout)
            if not proc.stdout.endswith("\n"):
                f.write("\n")
        if proc.stderr:
            f.write("[stderr]\n")
            f.write(proc.stderr)
            if not proc.stderr.endswith("\n"):
                f.write("\n")


def count_rows(csv_file: Path) -> int:
    if not csv_file.exists() or csv_file.stat().st_size == 0:
        return 0
    df = pd.read_csv(csv_file, encoding="utf-8-sig")
    return int(len(df))


def save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_one_stock(
    stock: StockItem,
    root_dir: Path,
    start_time: str,
    end_time: str,
    min_gap: int,
    zone_pct: float,
    keep_incomplete: bool,
    start_step: int = 1,
) -> dict:
    normalized_code = normalize_stock_code(stock.code)
    stock_dir = ensure_dir(root_dir / f"{safe_name(stock.name)}_{normalized_code}")
    log_file = stock_dir / "run.log"

    workspace = ensure_dir(stock_dir / WORKSPACE_DIRNAME)
    data_dir = ensure_dir(workspace / "数据获取")
    analysis_dir = ensure_dir(workspace / "分析和笔")
    indicator_dir = ensure_dir(workspace / "指标")
    pivot_dir = ensure_dir(workspace / "中枢")
    signal_dir = ensure_dir(workspace / "信号")
    report_dir = ensure_dir(workspace / "报告")

    visible_dirs = {key: ensure_dir(stock_dir / dirname) for key, dirname in STEP_DIRS.items()}

    indicator_file = indicator_dir / "ths_5min_indicators.csv"
    stroke_5min_file = analysis_dir / "ths_5min_2_1strokes.csv"

    result = {
        "股票名称": stock.name,
        "股票代码": normalized_code,
        "状态": "成功",
        "5min根数": 0,
        "指标根数": 0,
        "高低点次数": 0,
        "MACD三值次数": 0,
        "总指标次数": 0,
        "估算交易天数": 0.0,
        "目录": str(stock_dir),
        "错误": "",
    }

    try:
        if start_step <= 1:
            run_command(
                [
                    sys.executable,
                    str(BASE_DIR / "1-获取合并.py"),
                    "--code",
                    normalized_code,
                    "--start-time",
                    start_time,
                    "--end-time",
                    end_time,
                    "--output-dir",
                    str(data_dir),
                    *(["--keep-incomplete"] if keep_incomplete else []),
                ],
                cwd=BASE_DIR,
                log_file=log_file,
            )
            copy_files(data_dir.glob("*.csv"), visible_dirs["step1"])

            run_command(
                [
                    sys.executable,
                    str(BASE_DIR / "1b-获取指标.py"),
                    "--code",
                    normalized_code,
                    "--interval",
                    "5",
                    "--start-time",
                    start_time,
                    "--end-time",
                    end_time,
                    "--output",
                    str(indicator_file),
                ],
                cwd=BASE_DIR,
                log_file=log_file,
            )
            copy_files(indicator_dir.glob("*.csv"), visible_dirs["step1b"])

            run_command(
                [
                    sys.executable,
                    str(BASE_DIR / "2-分型和笔.py"),
                    "--input",
                    str(data_dir),
                    "--output-dir",
                    str(analysis_dir),
                    "--min-gap",
                    str(min_gap),
                ],
                cwd=BASE_DIR,
                log_file=log_file,
            )
            copy_files(analysis_dir.glob("ths_*_2_*.csv"), visible_dirs["step2"])

            run_command(
                [
                    sys.executable,
                    str(BASE_DIR / "3-线段.py"),
                    "--input",
                    str(analysis_dir),
                    "--output-dir",
                    str(analysis_dir),
                ],
                cwd=BASE_DIR,
                log_file=log_file,
            )
            copy_files(analysis_dir.glob("ths_*_3_*.csv"), visible_dirs["step3"])

        if start_step <= 4:
            reset_dir(pivot_dir)
            reset_dir(signal_dir)
            reset_dir(report_dir)
            reset_dir(visible_dirs["step4"])
            reset_dir(visible_dirs["step5"])
            reset_dir(visible_dirs["step6"])

            run_command(
                [
                    sys.executable,
                    str(BASE_DIR / "4-中枢.py"),
                    "--input",
                    str(analysis_dir),
                    "--out-dir",
                    str(pivot_dir),
                    "--mode",
                    "both",
                ],
                cwd=BASE_DIR,
                log_file=log_file,
            )
            copy_files(pivot_dir.glob("*.csv"), visible_dirs["step4"])

            run_command(
                [
                    sys.executable,
                    str(BASE_DIR / "5-背驰买点.py"),
                    "--strokes",
                    str(stroke_5min_file),
                    "--indicators",
                    str(indicator_file),
                    "--zone-pct",
                    str(zone_pct),
                    "--out-dir",
                    str(signal_dir),
                ],
                cwd=BASE_DIR,
                log_file=log_file,
            )
            copy_files(signal_dir.glob("*"), visible_dirs["step5"])

            run_command(
                [
                    sys.executable,
                    str(BASE_DIR / "6-生成报告.py"),
                    "--code",
                    normalized_code,
                    "--input-dir",
                    str(analysis_dir),
                    "--pivot-dir",
                    str(pivot_dir),
                    "--indicator-dir",
                    str(indicator_dir),
                    "--signal-dir",
                    str(signal_dir),
                    "--out-dir",
                    str(report_dir),
                ],
                cwd=BASE_DIR,
                log_file=log_file,
            )
            copy_files(report_dir.glob("*"), visible_dirs["step6"])

        raw_5min_file = data_dir / f"{normalized_code}_5min.csv"
        rows_5min = count_rows(raw_5min_file)
        rows_indicator = count_rows(indicator_file)
        cost_hl = rows_5min * 2
        cost_macd = rows_indicator * 3

        result["5min根数"] = rows_5min
        result["指标根数"] = rows_indicator
        result["高低点次数"] = cost_hl
        result["MACD三值次数"] = cost_macd
        result["总指标次数"] = cost_hl + cost_macd
        result["估算交易天数"] = round(rows_5min / 48, 2) if rows_5min else 0.0
    except subprocess.CalledProcessError as exc:
        result["状态"] = "失败"
        result["错误"] = f"命令失败，退出码={exc.returncode}"
    except Exception as exc:
        result["状态"] = "失败"
        result["错误"] = str(exc)

    save_json(stock_dir / "summary.json", result)
    return result


def save_selected_stocks(root_dir: Path, stocks: List[StockItem]) -> None:
    with (root_dir / "selected_stocks.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["股票代码", "股票名称"])
        for stock in stocks:
            writer.writerow([normalize_stock_code(stock.code), stock.name])


def load_selected_stocks(root_dir: Path) -> List[StockItem]:
    selected_path = root_dir / "selected_stocks.csv"
    stocks: List[StockItem] = []
    if selected_path.exists():
        with selected_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = str(row.get("股票代码", "")).strip()
                name = str(row.get("股票名称", "")).strip()
                if code and name:
                    stocks.append(StockItem(code=code, name=name))
    else:
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if child.name in STEP_DIRS.values():
                continue
            if "_" not in child.name:
                continue
            name, code = child.name.rsplit("_", 1)
            if code:
                stocks.append(StockItem(code=code, name=name))
    if not stocks:
        raise ValueError(f"无法从 {root_dir} 解析已选股票清单")
    return stocks


def save_summary(root_dir: Path, rows: List[dict], meta: dict) -> None:
    summary_df = pd.DataFrame(rows)
    summary_path = root_dir / "summary.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")

    total_payload = {
        "meta": meta,
        "stocks": rows,
        "totals": {
            "成功数": int(sum(1 for row in rows if row["状态"] == "成功")),
            "失败数": int(sum(1 for row in rows if row["状态"] != "成功")),
            "5min总根数": int(sum(int(row["5min根数"]) for row in rows)),
            "高低点总次数": int(sum(int(row["高低点次数"]) for row in rows)),
            "MACD三值总次数": int(sum(int(row["MACD三值次数"]) for row in rows)),
            "总指标次数": int(sum(int(row["总指标次数"]) for row in rows)),
        },
    }
    save_json(root_dir / "summary.json", total_payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="随机抽取 10 只股票做整条缠论流水线测试")
    parser.add_argument("--sample-size", type=int, default=10, help="随机抽样股票数量，默认 10")
    parser.add_argument("--seed", type=int, default=20260428, help="随机种子，默认 20260428")
    parser.add_argument("--lookback-days", type=int, default=30, help="默认回看天数")
    parser.add_argument("--start-time", default="", help="开始时间 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end-time", default="", help="结束时间 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--min-gap", type=int, default=4, help="第 2 步成笔最小 K 线间隔")
    parser.add_argument("--zone-pct", type=float, default=0.015, help="第 5 步买点区间比例")
    parser.add_argument("--keep-incomplete", action="store_true", help="是否保留未完成的 30/60min K 线")
    parser.add_argument(
        "--output-root",
        default="",
        help="测试输出根目录，默认写入 实现过程/测试/随机10股_时间戳",
    )
    parser.add_argument("--start-step", type=int, default=1, choices=[1, 4], help="从第几步开始跑，默认 1，可选 1 或 4")
    parser.add_argument("--resume-root", default="", help="续跑已有测试目录，传入后将复用 selected_stocks.csv")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_time, end_time = (
        (args.start_time, args.end_time)
        if args.start_time and args.end_time
        else calc_time_range(args.lookback_days)
    )

    if args.resume_root:
        root_dir = Path(args.resume_root).expanduser().resolve()
        ensure_dir(root_dir)
        stocks = load_selected_stocks(root_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else ensure_dir(TEST_ROOT / f"随机10股_{timestamp}")
        )
        ensure_dir(root_dir)
        stocks = pick_random_stocks(args.sample_size, args.seed)
        save_selected_stocks(root_dir, stocks)

    meta = {
        "sample_size": args.sample_size,
        "seed": args.seed,
        "start_time": start_time,
        "end_time": end_time,
        "lookback_days": args.lookback_days,
        "start_step": args.start_step,
        "resume_root": args.resume_root,
        "计数口径": "按5min返回根数计：high/low 每根算2次，DIFF/DEA/MACD 每根算3次，总次数=5min根数*2+指标根数*3",
    }
    save_json(root_dir / "meta.json", meta)

    rows: List[dict] = []
    for idx, stock in enumerate(stocks, start=1):
        print(f"[{idx}/{len(stocks)}] {stock.name} {normalize_stock_code(stock.code)}")
        row = run_one_stock(
            stock=stock,
            root_dir=root_dir,
            start_time=start_time,
            end_time=end_time,
            min_gap=args.min_gap,
            zone_pct=args.zone_pct,
            keep_incomplete=args.keep_incomplete,
            start_step=args.start_step,
        )
        rows.append(row)

    save_summary(root_dir, rows, meta)
    print(f"\n测试完成，输出目录: {root_dir}")
    print(f"总表: {root_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
