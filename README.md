# vegas_channel_failureprove

**维纳斯通道引擎（Vegas Channel Engine）** 的失败档案，属于 [量化失败合集 quant-failure-archive](https://github.com/runningshark/quant-failure-archive)。

判定为 **「无稳定 edge」**：看板曾显示的 **+87.4%** 来自前视偏差；修正后去龙虾短窗口 **−1.03%**。参数网格样本内最优 **+76.32%** 到样本外变成 **−4.13%**，36 组样本外全负。

- 结论和全部数字：[FAILURE_REPORT.md](FAILURE_REPORT.md)
- 复现：`python verify.py`（只需要 Python 3 标准库，不用装库），结果写到 `results/output.txt`
- 代码：`verify.py`（消融 / 前视偏差 / 长周期 / 参数样本内外验证，按时间戳对齐各币）
- 数据：`data/`，币安 4H + 日线快照，BTC/ETH/SOL/XRP 至 2026-11-23；龙虾永续至 2026-11-23
- 元数据：[entry.json](entry.json)

不构成任何投资建议。
