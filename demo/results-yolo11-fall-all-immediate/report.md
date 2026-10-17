# YOLO11 跌倒专用权重：完整本地数据集评测

共 69 段 / 11756 帧；完整性：True。

权重训练集与URFD的重叠未知，结果不能代表小车实机或独立泛化性能。

本轮仅重放既有预测缓存，未重新执行数据集GPU推理。下方GPU环境和吞吐来自原缓存生成过程。

固定设置：640×640、conf=0.25、IoU=0.7、FP32、CUDA；保留全部三类输出，无训练、无阈值扫描、无宽高比倒地规则。

所有带躺卧标注的片段均为正样本，包括主动躺下。事件命中要求信号至少覆盖一个已标注躺卧帧，避免提前误报虚增召回。

当前策略：即时告警；单帧 fallen 达到0.25即报警，不使用飞桨、不要求跟踪ID、不等待2秒。

| 指标 | 直接 fallen 输出 | 即时告警 |
|---|---:|---:|
| 躺卧片段命中 | 40/46 | 40/46 |
| 真实跌倒片段命中 | 28/30 | 28/30 |
| 主动躺卧片段命中 | 12/16 | 12/16 |
| 普通活动误报 | 12/23 | 12/23 |
| 躺卧帧召回率 | 40.7% | 40.7% |
| 帧级精确率 | 72.0% | 72.0% |

即时模式没有最短观察时间，全部46段正样本均纳入实际报出统计。
直接输出与告警逐帧一致；没有循环、冻结或延长短片，EOF 不补帧。事件按场景开始/结束记录，不将场景编号当作人员跟踪ID。

## 逐段结果

| 片段 | 选取原因 | 直接命中/误报 | 即时告警命中/误报 | 观察时间符合策略 |
|---|---|---|---|---|
| adl-01 | all_local_sequences | 误报 | 误报 | False |
| adl-02 | all_local_sequences | 误报 | 误报 | False |
| adl-03 | all_local_sequences | 误报 | 误报 | False |
| adl-04 | all_local_sequences | 误报 | 误报 | False |
| adl-05 | all_local_sequences | 误报 | 误报 | False |
| adl-06 | all_local_sequences | 误报 | 误报 | False |
| adl-07 | all_local_sequences | 误报 | 误报 | False |
| adl-09 | all_local_sequences | 无误报 | 无误报 | False |
| adl-10 | all_local_sequences | 命中 | 命中 | True |
| adl-11 | all_local_sequences | 漏报 | 漏报 | True |
| adl-12 | all_local_sequences | 误报 | 误报 | False |
| adl-13 | all_local_sequences | 无误报 | 无误报 | False |
| adl-14 | all_local_sequences | 无误报 | 无误报 | False |
| adl-15 | all_local_sequences | 误报 | 误报 | False |
| adl-16 | all_local_sequences | 无误报 | 无误报 | False |
| adl-17 | all_local_sequences | 无误报 | 无误报 | False |
| adl-18 | all_local_sequences | 无误报 | 无误报 | False |
| adl-19 | all_local_sequences | 误报 | 误报 | False |
| adl-20 | all_local_sequences | 误报 | 误报 | False |
| adl-21 | all_local_sequences | 漏报 | 漏报 | True |
| adl-22 | all_local_sequences | 漏报 | 漏报 | True |
| adl-23 | all_local_sequences | 漏报 | 漏报 | True |
| adl-24 | all_local_sequences | 无误报 | 无误报 | False |
| adl-25 | all_local_sequences | 无误报 | 无误报 | False |
| adl-26 | all_local_sequences | 无误报 | 无误报 | False |
| adl-27 | all_local_sequences | 无误报 | 无误报 | False |
| adl-28 | all_local_sequences | 误报 | 误报 | False |
| adl-29 | all_local_sequences | 无误报 | 无误报 | False |
| adl-30 | all_local_sequences | 命中 | 命中 | True |
| adl-31 | all_local_sequences | 命中 | 命中 | True |
| adl-32 | all_local_sequences | 命中 | 命中 | True |
| adl-33 | all_local_sequences | 命中 | 命中 | True |
| adl-34 | all_local_sequences | 命中 | 命中 | True |
| adl-35 | all_local_sequences | 命中 | 命中 | True |
| adl-36 | all_local_sequences | 命中 | 命中 | True |
| adl-37 | all_local_sequences | 命中 | 命中 | True |
| adl-38 | all_local_sequences | 命中 | 命中 | True |
| adl-39 | all_local_sequences | 命中 | 命中 | True |
| adl-40 | all_local_sequences | 命中 | 命中 | True |
| fall-01 | all_local_sequences | 命中 | 命中 | True |
| fall-02 | all_local_sequences | 命中 | 命中 | True |
| fall-03 | all_local_sequences | 命中 | 命中 | True |
| fall-04 | all_local_sequences | 命中 | 命中 | True |
| fall-05 | all_local_sequences | 命中 | 命中 | True |
| fall-06 | all_local_sequences | 命中 | 命中 | True |
| fall-07 | all_local_sequences | 命中 | 命中 | True |
| fall-08 | all_local_sequences | 命中 | 命中 | True |
| fall-09 | all_local_sequences | 命中 | 命中 | True |
| fall-10 | all_local_sequences | 命中 | 命中 | True |
| fall-11 | all_local_sequences | 命中 | 命中 | True |
| fall-12 | all_local_sequences | 命中 | 命中 | True |
| fall-13 | all_local_sequences | 命中 | 命中 | True |
| fall-14 | all_local_sequences | 命中 | 命中 | True |
| fall-15 | all_local_sequences | 命中 | 命中 | True |
| fall-16 | all_local_sequences | 命中 | 命中 | True |
| fall-17 | all_local_sequences | 命中 | 命中 | True |
| fall-18 | all_local_sequences | 漏报 | 漏报 | True |
| fall-19 | all_local_sequences | 命中 | 命中 | True |
| fall-20 | all_local_sequences | 命中 | 命中 | True |
| fall-21 | all_local_sequences | 命中 | 命中 | True |
| fall-22 | all_local_sequences | 命中 | 命中 | True |
| fall-23 | all_local_sequences | 命中 | 命中 | True |
| fall-24 | all_local_sequences | 命中 | 命中 | True |
| fall-25 | all_local_sequences | 漏报 | 漏报 | True |
| fall-26 | all_local_sequences | 命中 | 命中 | True |
| fall-27 | all_local_sequences | 命中 | 命中 | True |
| fall-28 | all_local_sequences | 命中 | 命中 | True |
| fall-29 | all_local_sequences | 命中 | 命中 | True |
| fall-30 | all_local_sequences | 命中 | 命中 | True |

## 结果解读

片段中至少命中一次不等于持续可靠识别：2373 个已标注躺卧帧中，966 帧输出 fallen，1312 帧没有任何检测框，95 帧仅输出其他姿态。
本次即时告警覆盖40/46段躺卧，普通活动误报12/23。取消确认时间不会改善模型本身的误分类。

## 环境与限制

GPU：NVIDIA GeForce RTX 4060 Laptop GPU；2.9.1+cu126；8.4.160；实际设备/类型：cuda:0 / torch.float32。
批量推理、读图与输出转CPU合计 53.59 秒，吞吐 219.4 FPS，不含加载、暖机、截图与视频编码；不是小车实时帧率。
该社区权重训练集划分和与URFD的重叠情况不明确，不能宣称独立泛化成绩。
证据在 evidence/，每段至少一张；两段本地视频仅为无逐帧人工标注的演示检查，不纳入准确率分母。

## 复现

在仓库根目录使用 .venv-demo/Scripts/python.exe demo/eval_yolo11_fall.py evaluate --scope all --confirm-seconds 0 --out-dir "D:\智能小车\ros2-smart-car\demo\results-yolo11-fall-all-immediate"。
evaluate 只重放缓存和生成报告；videos --video-replay-dir 指向已保存预测目录时不运行推理。默认不读取飞桨缓存或权重。

## 本地视频检查

| 视频 | 帧数 | fallen帧 | 即时告警帧 | 首次告警（秒） |
|---|---:|---:|---:|---:|
| 正常.mp4 | 139 | 0 | 0 | None |
| 倒地.mp4 | 108 | 84 | 84 | 0.2 |

两段视频均已重新解码检查输出帧数；它们没有逐帧人工标注，不计入准确率。
