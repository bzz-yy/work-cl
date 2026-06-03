from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


import os

ACCESS_TOKEN = os.getenv("THS_ACCESS_TOKEN", "aa62779ad95727b88574d08d2617ee8b406aa15a.signs_NzU1Nzg5MjQ0")
BASE_URL = "https://quantapi.51ifind.com/api/v1"

HEADERS = {
    "Content-Type": "application/json",
    "access_token": ACCESS_TOKEN,
}


INDICATORS = "low,high"
FUNCTION_PARA = {
    "CPS": "forward1",
    "Fill": "Previous",
    "Interval": "5",
}
LOOKBACK_DAYS = 30

MORNING_START = 9 * 60 + 35
MORNING_END = 11 * 60 + 30
AFTERNOON_START = 13 * 60 + 5
AFTERNOON_END = 15 * 60


@dataclass
class MergeKLine:
    time: pd.Timestamp
    thscode: str
    high: float
    low: float
    period: Optional[str] = None


def _calc_time_range(lookback_days: int = LOOKBACK_DAYS) -> tuple[str, str]:
    today = datetime.now().date()
    end_dt = datetime.combine(today, datetime.strptime("15:15", "%H:%M").time())
    start_dt = datetime.combine(
        today - timedelta(days=lookback_days),
        datetime.strptime("09:15", "%H:%M").time(),
    )
    fmt = "%Y-%m-%d %H:%M:%S"
    return start_dt.strftime(fmt), end_dt.strftime(fmt)


def normalize_stock_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code[:2] in ("83", "87", "43", "92"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _call_ths_hf(stock_code: str, start_time: str, end_time: str) -> dict:
    url = f"{BASE_URL}/high_frequency"
    body = {
        "codes": stock_code,
        "indicators": INDICATORS,
        "starttime": start_time,
        "endtime": end_time,
        "functionpara": FUNCTION_PARA,
    }
    resp = requests.post(url, json=body, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    if result.get("errorcode", -1) != 0:
        raise RuntimeError(
            f"THS接口报错 [{result.get('errorcode')}]: {result.get('errmsg', '未知错误')}"
        )
    return result


def _parse_response(result: dict) -> pd.DataFrame:
    tables = result.get("tables", [])
    if not tables:
        raise ValueError(f"接口响应无 tables 数据，keys: {list(result.keys())}")

    table0 = tables[0]
    col_data = {"time": table0["time"]}
    nested = table0.get("table", {})
    if isinstance(nested, dict):
        col_data.update(nested)
    else:
        col_data.update(
            {key: value for key, value in table0.items() if isinstance(value, list) and key != "time"}
        )

    df = pd.DataFrame(col_data)
    df.columns = [col.lower() for col in df.columns]
    keep_cols = [col for col in ["time", "thscode", "high", "low"] if col in df.columns]
    return df[keep_cols]


def _validate_and_clean(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    required = {"time", "high", "low"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"接口返回缺少必要列 {missing}，实际列: {list(df.columns)}"
        )

    clean_df = df.dropna(subset=["high", "low"]).copy()
    clean_df["time"] = pd.to_datetime(clean_df["time"])

    for col in ["high", "low"]:
        clean_df[col] = pd.to_numeric(clean_df[col], errors="coerce")

    if "thscode" not in clean_df.columns:
        clean_df["thscode"] = stock_code
    else:
        clean_df["thscode"] = clean_df["thscode"].fillna(stock_code).astype(str)

    clean_df = clean_df.dropna(subset=["high", "low"])
    clean_df = clean_df.sort_values("time").reset_index(drop=True)
    return clean_df[["time", "thscode", "high", "low"]]


def fetch_5min_data(
    stock_code: str,
    lookback_days: int = LOOKBACK_DAYS,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> pd.DataFrame:
    normalized_code = normalize_stock_code(stock_code)
    if start_time is None or end_time is None:
        start_time, end_time = _calc_time_range(lookback_days)
    result = _call_ths_hf(normalized_code, start_time, end_time)
    df = _parse_response(result)
    return _validate_and_clean(df, normalized_code)


def _classify_session(ts: pd.Timestamp) -> Optional[str]:
    minute_of_day = ts.hour * 60 + ts.minute
    if MORNING_START <= minute_of_day <= MORNING_END:
        return "morning"
    if AFTERNOON_START <= minute_of_day <= AFTERNOON_END:
        return "afternoon"
    return None


def aggregate_from_5min(
    df_5min: pd.DataFrame,
    period_minutes: int,
    keep_incomplete: bool = False,
) -> pd.DataFrame:
    if period_minutes % 5 != 0:
        raise ValueError("period_minutes 必须是 5 的整数倍。")

    bars_per_group = period_minutes // 5
    df = df_5min.copy()
    df["session"] = df["time"].apply(_classify_session)
    df = df[df["session"].notna()].copy()

    if df.empty:
        return pd.DataFrame(columns=["time", "thscode", "high", "low", "period"])

    df["trade_date"] = df["time"].dt.strftime("%Y-%m-%d")
    df["session_index"] = df.groupby(["trade_date", "session"]).cumcount()
    df["group_no"] = df["session_index"] // bars_per_group

    aggregated = (
        df.groupby(["trade_date", "session", "group_no"], as_index=False)
        .agg(
            time=("time", "last"),
            thscode=("thscode", "first"),
            high=("high", "max"),
            low=("low", "min"),
            bar_count=("time", "size"),
        )
    )

    if not keep_incomplete:
        aggregated = aggregated[aggregated["bar_count"] == bars_per_group].copy()

    aggregated["period"] = f"{period_minutes}min"
    aggregated = aggregated.sort_values("time").reset_index(drop=True)
    return aggregated[["time", "thscode", "high", "low", "period"]]


def _has_inclusion(last: MergeKLine, cur: MergeKLine) -> bool:
    return (last.high >= cur.high and last.low <= cur.low) or (
        cur.high >= last.high and cur.low <= last.low
    )


def _resolve_merge_direction(merged_rows: list[MergeKLine], cur: MergeKLine) -> str:
    last = merged_rows[-1]
    if len(merged_rows) >= 2:
        prev2 = merged_rows[-2]
        if last.high > prev2.high:
            return "up"
        if last.high < prev2.high:
            return "down"
        if last.low > prev2.low:
            return "up"
        if last.low < prev2.low:
            return "down"
    return "up" if cur.high >= last.high else "down"


def merge_inclusion(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    source = df.copy().sort_values("time").reset_index(drop=True)
    if "period" not in source.columns:
        source["period"] = None

    raw_klines = [
        MergeKLine(
            time=pd.to_datetime(row["time"]),
            thscode=str(row["thscode"]),
            high=float(row["high"]),
            low=float(row["low"]),
            period=row["period"] if pd.notna(row["period"]) else None,
        )
        for _, row in source.iterrows()
    ]

    merged_rows = [raw_klines[0]]
    for cur in raw_klines[1:]:
        last = merged_rows[-1]
        if _has_inclusion(last, cur):
            direction = _resolve_merge_direction(merged_rows, cur)
            if direction == "up":
                last.high = max(last.high, cur.high)
                last.low = max(last.low, cur.low)
            else:
                last.high = min(last.high, cur.high)
                last.low = min(last.low, cur.low)
            last.time = cur.time
            last.thscode = cur.thscode
            if cur.period is not None:
                last.period = cur.period
        else:
            merged_rows.append(cur)

    merged_df = pd.DataFrame(
        [
            {
                "time": row.time,
                "thscode": row.thscode,
                "high": row.high,
                "low": row.low,
                "period": row.period,
            }
            for row in merged_rows
        ]
    )
    if df.get("period") is None:
        merged_df = merged_df.drop(columns=["period"])
    else:
        merged_df["period"] = merged_df["period"].fillna("")
    return merged_df


def to_30min(df_5min: pd.DataFrame, keep_incomplete: bool = False) -> pd.DataFrame:
    return aggregate_from_5min(df_5min, 30, keep_incomplete=keep_incomplete)


def to_60min(df_5min: pd.DataFrame, keep_incomplete: bool = False) -> pd.DataFrame:
    return aggregate_from_5min(df_5min, 60, keep_incomplete=keep_incomplete)


def save_csv(df: pd.DataFrame, file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    export_df = df.copy()
    export_df["time"] = pd.to_datetime(export_df["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    export_df.to_csv(file_path, index=False, encoding="utf-8-sig")


def build_outputs(
    stock_code: str,
    output_dir: Path,
    lookback_days: int = LOOKBACK_DAYS,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    keep_incomplete: bool = False,
) -> dict[str, pd.DataFrame]:
    df_5min = fetch_5min_data(
        stock_code=stock_code,
        lookback_days=lookback_days,
        start_time=start_time,
        end_time=end_time,
    )
    df_30min = to_30min(df_5min, keep_incomplete=keep_incomplete)
    df_60min = to_60min(df_5min, keep_incomplete=keep_incomplete)
    df_5min_merged = merge_inclusion(df_5min)
    df_30min_merged = merge_inclusion(df_30min)
    df_60min_merged = merge_inclusion(df_60min)

    normalized_code = normalize_stock_code(stock_code)
    save_csv(df_5min, output_dir / f"{normalized_code}_5min.csv")
    save_csv(df_30min, output_dir / f"{normalized_code}_30min.csv")
    save_csv(df_60min, output_dir / f"{normalized_code}_60min.csv")
    save_csv(df_5min_merged, output_dir / f"{normalized_code}_5min_merged.csv")
    save_csv(df_30min_merged, output_dir / f"{normalized_code}_30min_merged.csv")
    save_csv(df_60min_merged, output_dir / f"{normalized_code}_60min_merged.csv")
    return {
        "5min": df_5min,
        "30min": df_30min,
        "60min": df_60min,
        "5min_merged": df_5min_merged,
        "30min_merged": df_30min_merged,
        "60min_merged": df_60min_merged,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="获取同花顺 5min 数据，并聚合为 30min 和 60min 数据。")
    parser.add_argument("--code", required=True, help="股票代码，例如 600519 或 600519.SH")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS, help="默认回看天数")
    parser.add_argument("--start-time", help="开始时间，格式: YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end-time", help="结束时间，格式: YYYY-MM-DD HH:MM:SS")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "输出" / "数据获取"),
        help="CSV 输出目录",
    )
    parser.add_argument(
        "--keep-incomplete",
        action="store_true",
        help="是否保留未凑满一个完整周期的 30min/60min K线",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    outputs = build_outputs(
        stock_code=args.code,
        output_dir=output_dir,
        lookback_days=args.lookback_days,
        start_time=args.start_time,
        end_time=args.end_time,
        keep_incomplete=args.keep_incomplete,
    )

    print(f"已生成 5min 数据: {len(outputs['5min'])} 行")
    print(f"已生成 30min 数据: {len(outputs['30min'])} 行")
    print(f"已生成 60min 数据: {len(outputs['60min'])} 行")
    print(f"已生成 5min 合并后数据: {len(outputs['5min_merged'])} 行")
    print(f"已生成 30min 合并后数据: {len(outputs['30min_merged'])} 行")
    print(f"已生成 60min 合并后数据: {len(outputs['60min_merged'])} 行")
    print(f"输出目录: {output_dir}")


if __name__ == "__main__":
    main()
