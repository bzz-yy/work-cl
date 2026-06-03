from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

"""
1b-获取指标.py
==============
一次性拉取某股票一段时间内的高频指标序列（默认 5min 的 DIF/DEA/MACD柱）。

说明
----
你已确认 THS_HF 支持 start/end 跨一个月且无返回限制，因此这里按“每个指标 1 次调用”
拉取全月序列，而不是按天拆分。

输出
----
CSV: time, thscode, diff, dea, macd, period
"""


import os

ACCESS_TOKEN = os.getenv("THS_ACCESS_TOKEN", "aa62779ad95727b88574d08d2617ee8b406aa15a.signs_NzU1Nzg5MjQ0")
BASE_URL = "https://quantapi.51ifind.com/api/v1"

HEADERS = {
    "Content-Type": "application/json",
    "access_token": ACCESS_TOKEN,
}


@dataclass
class IndicatorSpec:
    name: str
    ths_indicator: str
    out_col: str
    calculate_value: Optional[str] = None


SPECS = [
    # DIF/DEA/MACD 按同花顺 calculate 参数尝试
    IndicatorSpec(name="diff", ths_indicator="MACD", out_col="diff", calculate_value="12,26,9,DIFF"),
    IndicatorSpec(name="dea", ths_indicator="MACD", out_col="dea", calculate_value="12,26,9,DEA"),
    IndicatorSpec(name="macd", ths_indicator="MACD", out_col="macd", calculate_value="12,26,9,MACD"),
]


def normalize_stock_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code[:2] in ("83", "87", "43", "92"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _build_request_body(
    stock_code: str,
    indicator: str,
    interval: int,
    start_time: str,
    end_time: str,
    calculate_value: Optional[str] = None,
) -> dict:
    body = {
        "codes": stock_code,
        "indicators": indicator,
        "starttime": start_time,
        "endtime": end_time,
        "functionpara": {
            "CPS": "forward1",
            "Fill": "Previous",
            "Interval": str(interval),
            "calculate": {
                indicator: calculate_value or "12,26,9,MACD",
            },
        },
    }
    return body


def _call_ths_hf(
    stock_code: str,
    indicator: str,
    interval: int,
    start_time: str,
    end_time: str,
    calculate_value: Optional[str] = None,
) -> dict:
    url = f"{BASE_URL}/high_frequency"
    body = _build_request_body(
        stock_code=stock_code,
        indicator=indicator,
        interval=interval,
        start_time=start_time,
        end_time=end_time,
        calculate_value=calculate_value,
    )
    resp = requests.post(url, json=body, headers=HEADERS, timeout=120)
    resp.raise_for_status()
    result = resp.json()
    if result.get("errorcode", -1) != 0:
        raise RuntimeError(
            f"THS接口报错 [{result.get('errorcode')}]: {result.get('errmsg', '未知错误')}"
        )
    return result


def _parse_table(result: dict) -> pd.DataFrame:
    """
    兼容两种返回结构：
    - tables[0] 内嵌 table dict（你在 1-获取合并.py 里遇到过）
    - tables[0] 直接有多个 list 字段
    """
    tables = result.get("tables", [])
    if not tables:
        raise ValueError(f"接口响应无 tables 数据，keys: {list(result.keys())}")

    t0 = tables[0]
    col_data = {"time": t0.get("time", [])}
    nested = t0.get("table", {})
    if isinstance(nested, dict) and nested:
        col_data.update(nested)
    else:
        for k, v in t0.items():
            if isinstance(v, list) and k != "time":
                col_data[k] = v

    df = pd.DataFrame(col_data)
    df.columns = [c.lower() for c in df.columns]
    return df


def _pick_value_col(df: pd.DataFrame) -> str:
    """
    THS 返回的指标列名不完全一致，这里做一个稳健选择：
    - 去掉 time / thscode / codes 等字段后，如果只剩 1 列，就用它
    - 否则优先找 macd 字样，否则退化到第 1 个数值列
    """
    ban = {"time", "thscode", "codes", "code"}
    candidates = [c for c in df.columns if c not in ban]
    if not candidates:
        raise ValueError(f"未找到指标列，实际列: {list(df.columns)}")
    if len(candidates) == 1:
        return candidates[0]

    for key in ("macd", "dif", "dea"):
        for c in candidates:
            if key in c:
                return c

    # 退化：选第一个能转成数值的列
    return candidates[0]


def fetch_indicator_series(
    stock_code: str,
    indicator: str,
    out_col: str,
    interval: int,
    start_time: str,
    end_time: str,
    calculate_value: Optional[str] = None,
) -> pd.DataFrame:
    result = _call_ths_hf(
        stock_code,
        indicator,
        interval,
        start_time,
        end_time,
        calculate_value,
    )
    # 某些权限/指标不支持时，会出现 errorcode=0 但 dataVol=0 且 table 为空
    if result.get("dataVol", None) == 0:
        tables = result.get("tables", [])
        if tables and isinstance(tables[0], dict):
            nested = tables[0].get("table", None)
            if isinstance(nested, dict) and len(nested) == 0:
                raise RuntimeError(
                    f"指标返回为空（dataVol=0, table为空）。"
                    f"可能原因：当前 access_token 无该指标权限 / 指标名不适配此接口。"
                    f"indicator={indicator}, "
                    f"calculate_value={calculate_value}, "
                    f"interval={interval}, time=[{start_time},{end_time}]"
                )
    df = _parse_table(result)
    df["time"] = pd.to_datetime(df["time"])
    if "thscode" not in df.columns:
        df["thscode"] = stock_code
    val_col = _pick_value_col(df)
    df[out_col] = pd.to_numeric(df[val_col], errors="coerce")
    return df[["time", "thscode", out_col]].dropna(subset=[out_col]).sort_values("time")


def build_indicators(
    stock_code: str,
    interval: int,
    start_time: str,
    end_time: str,
) -> pd.DataFrame:
    code = normalize_stock_code(stock_code)
    merged: Optional[pd.DataFrame] = None

    for spec in SPECS:
        part = fetch_indicator_series(
            stock_code=code,
            indicator=spec.ths_indicator,
            out_col=spec.out_col,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            calculate_value=spec.calculate_value,
        )
        merged = part if merged is None else merged.merge(part, on=["time", "thscode"], how="outer")

    assert merged is not None
    merged = merged.sort_values("time").reset_index(drop=True)
    # 缺失用前值填充（你 functionpara 里 Fill=Previous，理论上不应缺，但保险）
    for c in ("diff", "dea", "macd"):
        if c in merged.columns:
            merged[c] = merged[c].ffill()
    merged["period"] = f"{interval}min"
    return merged


def save_csv(df: pd.DataFrame, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    export = df.copy()
    export["time"] = pd.to_datetime(export["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    export.to_csv(out, index=False, encoding="utf-8-sig")


def _default_time_range(days: int = 30) -> tuple[str, str]:
    today = datetime.now().date()
    end_dt = datetime.combine(today, datetime.strptime("15:15", "%H:%M").time())
    start_dt = datetime.combine(today - timedelta(days=days), datetime.strptime("09:15", "%H:%M").time())
    fmt = "%Y-%m-%d %H:%M:%S"
    return start_dt.strftime(fmt), end_dt.strftime(fmt)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="获取 THS 高频 DIF/DEA/MACD 序列（按月一次取回）")
    p.add_argument("--code", required=True, help="股票代码，例如 600519 或 600519.SH")
    p.add_argument("--interval", type=int, default=5, help="周期分钟数：默认 5")
    p.add_argument("--start-time", default="", help="开始时间 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--end-time", default="", help="结束时间 YYYY-MM-DD HH:MM:SS")
    p.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "输出" / "指标" / "ths_5min_indicators.csv"),
        help="输出 CSV 路径",
    )
    p.add_argument("--lookback-days", type=int, default=30, help="未传 start/end 时的回看天数")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.start_time and args.end_time:
        start_time, end_time = args.start_time, args.end_time
    else:
        start_time, end_time = _default_time_range(args.lookback_days)
    df = build_indicators(args.code, args.interval, start_time, end_time)
    output_path = Path(args.output)
    save_csv(df, output_path)
    print(f"已输出指标: {output_path}  行数={len(df)}  period={args.interval}min")


if __name__ == "__main__":
    main()
