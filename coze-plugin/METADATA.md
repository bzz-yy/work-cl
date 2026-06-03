# Coze 插件 Metadata 配置说明

把以下字段在 Coze IDE 的 Input/Output 配置面板里录一遍，typings 会自动生成对应 `Input` / `Output` 类。

## Input

| 字段名 | 类型 | 必填 | 默认 | 描述 |
|---|---|---|---|---|
| `symbol` | string | 是 | — | 股票代码，例：`600519.SH` / `000001.SZ` |
| `access_token` | string | 否 | 空 | 同花顺 iFinD access_token，留空使用内置默认值 |
| `lookback_days` | integer | 否 | 30 | 回看天数（含交易日和非交易日） |
| `levels` | string | 否 | `5,15,60` | 要分析的周期，逗号分隔，单位分钟 |

## Output

| 字段名 | 类型 | 描述 |
|---|---|---|
| `success` | boolean | 是否成功（至少一个级别成功即 true） |
| `error` | string | 错误信息汇总（多个级别失败时拼接） |
| `data` | object | 完整结构化结果，含 `symbol` / `as_of` / `levels.{5,15,60}` |
| `facts` | array of string | 事实原子清单，给上层 LLM 解读用 |
| `narrative_fallback` | string | 规则拼接的兜底人话解读，未命中条件的句子会自动跳过 |

### `data.levels.{level}` 的结构示例

```json
{
  "bar_count": 1024,
  "bi_count": 18,
  "zhongshu_count": 2,
  "trend": "up",
  "confidence": "high",
  "last_bi": {
    "dir": "up",
    "start_time": "...", "end_time": "...",
    "low": 1685.2, "high": 1702.5,
    "macd_area": 1.82, "dif_peak": 0.31, "dea_avg": 0.18,
    "divergence": {"kind":"top_divergence","area_ratio":0.65,"dea_zone":"above_zero"}
  },
  "last_zhongshu": {
    "zg": 1690.0, "zd": 1660.0,
    "high": 1702.5, "low": 1650.0,
    "bi_count": 5, "status": "extending",
    "start_time": "...", "end_time": "..."
  },
  "position_vs_zs": "above",
  "last_close": 1696.0,
  "divergences": [...],
  "realtime_alert": {
    "current_bar": {"time":"...","open":..,"high":..,"low":..,"close":..},
    "bar_minutes": 5,
    "potential_impact": [
      {"scenario":"...","result":"...","next_action":"..."}
    ]
  },
  "all_bi_summary": [...],
  "all_zs_summary": [...]
}
```

## Coze 工作流推荐串法

```
[插件节点 (本插件)]
    ↓ outputs.facts + outputs.data
[LLM 节点]
    System: "你是缠论分析助教。根据下面客观事实清单，给学员生成结构化解读..."
    User:   {{facts}} + {{data 摘要}}
    ↓
[输出展示]
```

如果工作流里没有 LLM 节点，直接展示 `narrative_fallback` 也能用。

## 依赖

只用 `requests` + 标准库。如果 Coze 运行时不带 `requests`，需在依赖配置里加上：

```
requests>=2.28
```

## 网络白名单

要允许出站访问：`https://quantapi.51ifind.com`
