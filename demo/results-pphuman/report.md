# PP-Human 官方跌倒模型：本地 URFD 评测

数据：69 段、11756 帧，30 FPS。

模型：官方 PP-YOLOE + 跟踪 + HRNet + ST-GCN，50 帧骨骼窗口，argmax 判类别，未调整阈值。

**训练集重叠：官方说明 ST-GCN 训练包含 URFD；这里是本地回归结果，不是独立泛化性能。**

## 事件级结果

| 指标 | PP-Human | 原宽高比方案（已有报告） |
|---|---:|---:|
| 跌倒报出 | 16 | 16 |
| ADL 误报段 | 15 | 14 |
| 主动躺下误报段 | 12 | 13 |
| 普通 ADL 误报段 | 3 | 1 |
| ADL 告警帧 | 1228 | 855 |
| 告警延迟中位数（帧，负数表示早于躺地） | 19 | 9 |

事件召回率：53.3%（16/30）。
ADL 误报：15/39 段。
漏报：fall-05, fall-08, fall-09, fall-15, fall-17, fall-19, fall-21, fall-23, fall-24, fall-25, fall-27, fall-28, fall-29, fall-30。

仅统计跌倒过渡开始后新产生的模型跌倒判断：16/30 段。
过渡前已产生跌倒判断的段：无。

## 性能与评测口径

GPU/CPU：GPU；Paddle 3.2.2；FP32。
CUDA：12.6；cuDNN 编译版本：9.9.0；运行库包：9.9.0.52。
逐帧读取、三模型推理、后处理总耗时 1618.3 秒，吞吐 7.3 FPS（不含模型加载）。

事件级任何一次跌倒判断即算报出；ADL 主动躺下也计入误报。另存 raw_event_summary，防止 80 帧显示保持掩盖模型输出。
缓存中的 action.score 是官方输出的原始 logit，不是 0～1 概率；分类采用 argmax，没有将其作为置信度阈值。
帧级只在跌倒段的躺地帧计正样本，排除过渡和缺失标注，属于辅助诊断。
跟踪、骨骼缓存和显示历史每段重置；不将视频末尾不足 50 帧的缓冲区额外送入模型，以保持官方视频流程。

## 每段结果

| 序列 | 报出/误报 | 首次告警帧 | 延迟（秒） | ADL 告警帧 |
|---|---|---:|---:|---:|
| adl-01 | 正常 | None | None | 0 |
| adl-02 | 正常 | None | None | 0 |
| adl-03 | 正常 | None | None | 0 |
| adl-04 | 正常 | None | None | 0 |
| adl-05 | 正常 | None | None | 0 |
| adl-06 | 正常 | None | None | 0 |
| adl-07 | 正常 | None | None | 0 |
| adl-09 | 正常 | None | None | 0 |
| adl-10 | 误报 | 200 | None | 80 |
| adl-11 | 误报 | 200 | None | 101 |
| adl-12 | 误报 | 228 | None | 23 |
| adl-13 | 误报 | 245 | None | 21 |
| adl-14 | 正常 | None | None | 0 |
| adl-15 | 正常 | None | None | 0 |
| adl-16 | 正常 | None | None | 0 |
| adl-17 | 正常 | None | None | 0 |
| adl-18 | 正常 | None | None | 0 |
| adl-19 | 正常 | None | None | 0 |
| adl-20 | 正常 | None | None | 0 |
| adl-21 | 误报 | 178 | None | 80 |
| adl-22 | 正常 | None | None | 0 |
| adl-23 | 正常 | None | None | 0 |
| adl-24 | 误报 | 50 | None | 21 |
| adl-25 | 正常 | None | None | 0 |
| adl-26 | 正常 | None | None | 0 |
| adl-27 | 正常 | None | None | 0 |
| adl-28 | 正常 | None | None | 0 |
| adl-29 | 正常 | None | None | 0 |
| adl-30 | 误报 | 231 | None | 80 |
| adl-31 | 误报 | 100 | None | 143 |
| adl-32 | 误报 | 171 | None | 30 |
| adl-33 | 误报 | 152 | None | 49 |
| adl-34 | 误报 | 100 | None | 92 |
| adl-35 | 误报 | 108 | None | 160 |
| adl-36 | 正常 | None | None | 0 |
| adl-37 | 正常 | None | None | 0 |
| adl-38 | 误报 | 176 | None | 170 |
| adl-39 | 误报 | 171 | None | 100 |
| adl-40 | 误报 | 253 | None | 78 |
| fall-01 | 报出 | 133 | 0.667 | 0 |
| fall-02 | 报出 | 100 | 1.0 | 0 |
| fall-03 | 报出 | 200 | 0.733 | 0 |
| fall-04 | 报出 | 50 | 0.2 | 0 |
| fall-05 | 漏报 | None | None | 0 |
| fall-06 | 报出 | 50 | 0.3 | 0 |
| fall-07 | 报出 | 151 | 1.2 | 0 |
| fall-08 | 漏报 | None | None | 0 |
| fall-09 | 漏报 | None | None | 0 |
| fall-10 | 报出 | 83 | 0.433 | 0 |
| fall-11 | 报出 | 100 | 0.7 | 0 |
| fall-12 | 报出 | 100 | 1.5 | 0 |
| fall-13 | 报出 | 72 | -0.067 | 0 |
| fall-14 | 报出 | 50 | 0.133 | 0 |
| fall-15 | 漏报 | None | None | 0 |
| fall-16 | 报出 | 50 | 0.1 | 0 |
| fall-17 | 漏报 | None | None | 0 |
| fall-18 | 报出 | 55 | 0.3 | 0 |
| fall-19 | 漏报 | None | None | 0 |
| fall-20 | 报出 | 104 | 1.867 | 0 |
| fall-21 | 漏报 | None | None | 0 |
| fall-22 | 报出 | 53 | 0.633 | 0 |
| fall-23 | 漏报 | None | None | 0 |
| fall-24 | 漏报 | None | None | 0 |
| fall-25 | 漏报 | None | None | 0 |
| fall-26 | 报出 | 46 | 0.1 | 0 |
| fall-27 | 漏报 | None | None | 0 |
| fall-28 | 漏报 | None | None | 0 |
| fall-29 | 漏报 | None | None | 0 |
| fall-30 | 漏报 | None | None | 0 |
