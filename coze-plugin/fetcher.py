"""
fetcher.py
==========
从同花顺 iFinD quantapi 拉取高频 K 线 + MACD 数据。

接口（已在 1-获取合并.py / 1b-获取指标.py 中验证）：
  POST https://quantapi.51ifind.com/api/v1/high_frequency
  header: {Content-Type: application/json, access_token: <token>}

返回的 K 线字典:
  {"time","open","high","low","close","dif","dea","macd"}
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json

import requests

BASE_URL = "https://quantapi.51ifind.com/api/v1"
DEFAULT_TOKEN = "aa62779ad95727b88574d08d2617ee8b406aa15a.signs_NzU1Nzg5MjQ0"


def _post(endpoint: str, payload: Dict[str, Any], token: str,
          timeout: int = 15) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "access_token": token}
    r = requests.post(f"{BASE_URL}/{endpoint}", headers=headers,
                      data=json.dumps(payload), timeout=timeout)
    r.raise_for_status()
    return r.json()


def _ths_hf(thscode: str, indicators: str, interval: int,
            start: str, end: str, token: str,
            macd_option: Optional[str] = None) -> List[Dict[str, Any]]:
    """高频接口；indicators 用分号分隔；返回 list of {time, <indicator fields>}。"""
    para = f"CPS:forward1,Fill:Previous,Interval:{interval}"
    if macd_option:
        ind = f"{indicators}=MACD_Option:{macd_option}"
    else:
        ind = indicators
    payload = {
        "codes": thscode,
        "indicators": ind,
        "functionpara": para,
        "starttime": start,
        "endtime": end,
    }
    data = _post("high_frequency", payload, token)
    tables = data.get("tables") or []
    if not tables:
        return []
    t0 = tables[0]
    times = t0.get("time") or []
    table = t0.get("table") or {}
    rows: List[Dict[str, Any]] = []
    for i, tm in enumerate(times):
        row: Dict[str, Any] = {"time": tm}
        for k, v in table.items():
            row[k] = v[i] if i < len(v) else None
        rows.append(row)
    return rows


def fetch_kline_with_macd(thscode: str, interval: int,
                          start: str, end: str,
                          token: str = DEFAULT_TOKEN) -> List[Dict[str, Any]]:
    """拉取一个级别的 OHLC + DIF/DEA/MACD，合并成统一字典列表。
    interval: 5/15/60 (分钟)
    """
    ohlc = _ths_hf(thscode, "open;high;low;close", interval, start, end, token)
    dif = _ths_hf(thscode, "MACD", interval, start, end, token)
    dea = _ths_hf(thscode, "MACD", interval, start, end, token, macd_option="2")
    macd = _ths_hf(thscode, "MACD", interval, start, end, token, macd_option="3")

    def to_map(rows: List[Dict[str, Any]], val_key_pref: str) -> Dict[str, float]:
        m: Dict[str, float] = {}
        for r in rows:
            t = r.get("time")
            if not t:
                continue
            # 找第一个非时间的数值字段
            for k, v in r.items():
                if k == "time":
                    continue
                if v is not None:
                    m[t] = float(v)
                    break
        return m

    dif_m = to_map(dif, "dif")
    dea_m = to_map(dea, "dea")
    macd_m = to_map(macd, "macd")

    result = []
    for r in ohlc:
        t = r.get("time")
        if not t:
            continue
        result.append({
            "time": t,
            "open": float(r.get("open") or 0),
            "high": float(r.get("high") or 0),
            "low": float(r.get("low") or 0),
            "close": float(r.get("close") or 0),
            "dif": dif_m.get(t, 0.0),
            "dea": dea_m.get(t, 0.0),
            "macd": macd_m.get(t, 0.0),
        })
    return result


def time_range(lookback_days: int) -> tuple:
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days)
    return (start_dt.strftime("%Y-%m-%d 09:15:00"),
            end_dt.strftime("%Y-%m-%d 15:15:00"))
