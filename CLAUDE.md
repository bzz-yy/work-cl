# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

缠论（Chanlun/Chan Theory）形态学分析工具。Python 流水线项目，从同花顺 iFinD API 拉取 A 股高频数据，按缠论步骤识别分型、笔、线段、中枢、背驰买点，最终生成 Markdown/JSON 报告。

## Running the Pipeline

### 一键运行（推荐）

```bash
python 7-一键运行.py --code 600519 --lookback-days 30
```

常用参数：
- `--code`：股票代码（支持 600519、600519.SH、000001.SZ 等格式，会自动归一化）
- `--lookback-days`：回看天数，默认 30
- `--start-time / --end-time`：自定义时间范围，格式 `YYYY-MM-DD HH:MM:SS`
- `--min-gap`：成笔最小 K 线间隔，默认 4
- `--zone-pct`：买点区间比例，默认 0.015（±1.5%）
- `--keep-incomplete`：第 1 步保留未完成的 30min/60min K 线

### 分步运行

各脚本可独立执行，通过 `--input` / `--output-dir` 传递文件：

```bash
python 1-获取合并.py --code 600519.SH --start-time "2026-04-01 09:15:00" --end-time "2026-04-27 15:15:00" --output-dir 输出/数据获取
python 1b-获取指标.py --code 600519.SH --interval 5 --start-time ... --end-time ... --output 输出/指标/ths_5min_indicators.csv
python 2-分型和笔.py --input 输出/数据获取 --output-dir 输出/分析和笔 --min-gap 4
python 3-线段.py --input 输出/分析和笔 --output-dir 输出/分析和笔
python 4-中枢.py --input 输出/分析和笔 --out-dir 输出/中枢 --mode both
python 5-背驰买点.py --strokes 输出/分析和笔/ths_5min_2_1strokes.csv --indicators 输出/指标/ths_5min_indicators.csv --zone-pct 0.015 --out-dir 输出/信号
python 6-生成报告.py --code 600519.SH --input-dir 输出/分析和笔 --pivot-dir 输出/中枢 --indicator-dir 输出/指标 --signal-dir 输出/信号 --out-dir 输出/报告
```

### 随机测试

```bash
python 8-随机10股测试.py --count 10 --lookback-days 30
```

## Architecture

### 7-Step Pipeline

| 步骤 | 脚本 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| 1 | `1-获取合并.py` | iFinD API | `*_merged.csv` | 拉取 5min K 线，合并为 5min/30min/60min |
| 1b | `1b-获取指标.py` | iFinD API | `*_indicators.csv` | 拉取 diff/dea/macd 指标序列 |
| 2 | `2-分型和笔.py` | `*_merged.csv` | `*_2_1strokes.csv`, `*_2_2valid_fractals.csv` | 识别顶底分型，构建笔 |
| 3 | `3-线段.py` | `*_2_1strokes.csv` | `*_3_segments.csv` | 笔 → 线段，含 case1/case2/data_end 三种终结方式 |
| 4 | `4-中枢.py` | `*_2_1strokes.csv`, `*_3_segments.csv` | `*_4_pivot_stroke.csv`, `*_4_pivot_segment.csv` | 识别中枢（ZG/ZD），支持扩展与级别提升 |
| 5 | `5-背驰买点.py` | `*_2_1strokes.csv`, `*_indicators.csv` | `*_5_signals.csv` | 背驰识别 + 买点区间提示 |
| 6 | `6-生成报告.py` | 前述全部 | `*_report.md`, `*_report.json` | 汇总结构化报告 |
| 7 | `7-一键运行.py` | - | 协调 1-6 步 | 流水线总控 |
| 8 | `8-随机10股测试.py` | - | 测试工作区 | 随机抽样批量验证 |

### Key Data Structures

- **KLine** (`dt`, `high`, `low`, `index`) — 基础 K 线
- **Fractal** (`fx_type`: top/bottom, `price`, `klines[3]`) — 顶底分型，由连续 3 根 K 线判定
- **Stroke** (`direction`: up/down, `start_fractal`, `end_fractal`, `kline_count`) — 笔，相邻异向分型经确认后连成
- **Segment** (`start_stroke_idx`, `end_stroke_idx`, `termination`) — 线段，由至少 3 笔重叠开始，经 case1/case2 终结
- **Pivot** (`ZG`, `ZD`, `status`: active/complete/level_up) — 中枢区间，取最初 3 单元交集固定 ZG/ZD，后续只扩展不收缩
- **Signal** — 背驰买点，以 diff 为主、macd 柱辅助判定

### File Conventions

- 所有中间数据通过 CSV 在 `输出/` 下交换，脚本之间无 Python import 依赖
- 文件名含周期标识：`5min`、`30min`、`60min`
- 脚本使用 `encoding="utf-8-sig"` 读写 CSV，兼容 Excel 中文显示
- 第 2/3/4 步的 `_infer_period()` 函数从输入文件名推断周期，决定输出文件名

## Data Source

- 接口：同花顺 iFinD 量化 API (`quantapi.51ifind.com/api/v1`)
- Token：硬编码在 `1-获取合并.py` 和 `1b-获取指标.py` 的 `ACCESS_TOKEN`
- 限制：调用次数和指标计数按 iFinD 配额计费，`7-一键运行.py` 中会打印预估用量

## Dependencies

项目无 `requirements.txt` 或 `pyproject.toml`，运行时仅依赖：

```
pandas
requests
```

其余均为标准库（`argparse`, `csv`, `dataclasses`, `datetime`, `pathlib`, `subprocess` 等）。

## Notes

- **5min 为主操作级别**：虽然第 1 步会生成 30min/60min 合并 K 线，但第 5 步背驰买点和第 6 步报告以 5min 数据为核心
- **笔构建规则**：`MIN_GAP`（默认 4）表示顶底分型之间至少间隔 4 根 K 线索引差才可成笔；方向必须严格交替
- **线段终结**：case1 = 有缺口的新极端点直接终结；case2 = 无缺口时等次级别回抽确认；data_end = 数据末尾未终结
- **中枢判定**：离开中枢要求"完全离开"（向上离开需 `unit.low > ZG`，向下离开需 `unit.high < ZD`），而非简单 high/low 触碰
- **随机测试脚本（8-随机10股测试.py）**：会动态 import 父目录 `../技术方案/获取股票代码/stock_search.py` 获取股票池，并在 `测试/` 下为每只股票创建工作区
