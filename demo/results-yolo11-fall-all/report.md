# YOLO11 跌倒专用权重：完整本地数据集评测

共 69 段 / 11756 帧；完整性：True。

覆盖现有全部69段本地数据；权重训练集与URFD的重叠未知，结果仍不能代表小车实机或独立泛化性能。

固定设置：640×640、conf=0.25、IoU=0.7、FP32、CUDA；保留全部三类输出，无训练、无阈值扫描、无宽高比倒地规则。

所有带躺卧标注的片段均为正样本，包括主动躺下。事件命中要求信号至少覆盖一个已标注躺卧帧，避免提前误报虚增召回。

| 指标 | 直接 fallen 输出 | 确认2秒告警 |
|---|---:|---:|
| 躺卧片段命中 | 40/46 | 3/46 |
| 真实跌倒片段命中 | 28/30 | 1/30 |
| 主动躺卧片段命中 | 12/16 | 2/16 |
| 普通活动误报 | 12/23 | 0/23 |
| 躺卧帧召回率 | 40.7% | 3.9% |
| 帧级精确率 | 72.0% | 100.0% |

正样本中，连续标注躺卧的实际观察跨度达到2秒：18；不足2秒：28。
没有循环、冻结或延长短片；EOF 不推进计时。确认层采用全部三类框的独立 IoU 关联，短暂缺测暂停计时；原始检测指标不受跟踪成败影响。

## 原方案与飞桨对照

完整覆盖此前飞桨成功和失败的片段，已解除只看失败子集的选择偏差。
原宽高比方案在所选数据的历史告警：正样本 29/46，普通活动 1/23。
原方案使用0.5秒规则，飞桨包含80帧显示保持。它们与新模型直接输出、2秒确认属于不同策略；历史“曾报警”不保证对齐躺卧标注，不能混成严格同口径准确率。
历史全集业务口径仅供背景：原方案29/46、普通活动误报1/23；飞桨28/46、误报3/23。下方另按相同躺卧标注重算对照。

| 同一标注对齐口径 | 飞桨原告警状态 | YOLO直接输出 | 两者OR覆盖 |
|---|---:|---:|---:|
| 躺卧片段 | 28/46 | 40/46 | 43/46 |
| 真实跌倒片段 | 16/30 | 28/30 | 29/30 |
| 普通活动误报 | 3/23 | 12/23 | 14/23 |

OR覆盖是已有缓存的离线对照：飞桨原告警含80帧显示保持，YOLO使用直接输出，没有共同的2秒确认，也没有部署双模型系统。
飞桨能覆盖而YOLO漏掉的躺卧片段：adl-11, adl-21, fall-18。

## 本次续跑

缓存复用及新增统计：{'reused_existing': 0, 'reused_prior_subset': 21, 'inferred_sequences': 48, 'inferred_frames': 8726}。

| 分组 | 躺卧片段命中 | 真实跌倒命中 | 普通活动误报 |
|---|---:|---:|---:|
| prior_paddle_failures | 15/18 | 13/14 | 1/3 |
| remaining | 25/28 | 15/16 | 11/20 |

## 逐段结果

| 片段 | 飞桨历史结果 | 直接命中/误报 | 2秒命中/误报 | 躺卧观察满2秒 |
|---|---|---|---|---|
| adl-01 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-02 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-03 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-04 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-05 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-06 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-07 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-09 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-10 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-11 | paddle_reported_lying | 漏报 | 漏报 | True |
| adl-12 | ordinary_activity_false_alarm | 误报 | 无误报 | False |
| adl-13 | ordinary_activity_false_alarm | 无误报 | 无误报 | False |
| adl-14 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-15 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-16 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-17 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-18 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-19 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-20 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-21 | paddle_reported_lying | 漏报 | 漏报 | True |
| adl-22 | missed_lying_activity | 漏报 | 漏报 | True |
| adl-23 | missed_lying_activity | 漏报 | 漏报 | True |
| adl-24 | ordinary_activity_false_alarm | 无误报 | 无误报 | False |
| adl-25 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-26 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-27 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-28 | paddle_normal_activity_correct | 误报 | 无误报 | False |
| adl-29 | paddle_normal_activity_correct | 无误报 | 无误报 | False |
| adl-30 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-31 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-32 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-33 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-34 | paddle_reported_lying | 命中 | 命中 | True |
| adl-35 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-36 | missed_lying_activity | 命中 | 漏报 | True |
| adl-37 | missed_lying_activity | 命中 | 命中 | True |
| adl-38 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-39 | paddle_reported_lying | 命中 | 漏报 | True |
| adl-40 | paddle_reported_lying | 命中 | 漏报 | True |
| fall-01 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-02 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-03 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-04 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-05 | missed_fall | 命中 | 漏报 | False |
| fall-06 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-07 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-08 | missed_fall | 命中 | 漏报 | False |
| fall-09 | missed_fall | 命中 | 漏报 | False |
| fall-10 | paddle_reported_lying | 命中 | 漏报 | True |
| fall-11 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-12 | paddle_reported_lying | 命中 | 命中 | False |
| fall-13 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-14 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-15 | missed_fall | 命中 | 漏报 | False |
| fall-16 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-17 | missed_fall | 命中 | 漏报 | False |
| fall-18 | paddle_reported_lying | 漏报 | 漏报 | False |
| fall-19 | missed_fall | 命中 | 漏报 | False |
| fall-20 | paddle_reported_lying | 命中 | 漏报 | True |
| fall-21 | missed_fall | 命中 | 漏报 | False |
| fall-22 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-23 | missed_fall | 命中 | 漏报 | False |
| fall-24 | missed_fall | 命中 | 漏报 | False |
| fall-25 | missed_fall | 漏报 | 漏报 | False |
| fall-26 | paddle_reported_lying | 命中 | 漏报 | False |
| fall-27 | missed_fall | 命中 | 漏报 | False |
| fall-28 | missed_fall | 命中 | 漏报 | False |
| fall-29 | missed_fall | 命中 | 漏报 | False |
| fall-30 | missed_fall | 命中 | 漏报 | False |

## 结果解读

片段中至少命中一次不等于持续可靠识别：2373 个已标注躺卧帧中，966 帧输出 fallen，1312 帧没有任何检测框，95 帧仅输出其他姿态。
46段躺卧片段中直接命中为 40 段，但2秒确认仅 3 段；除短片外，持续检测和身份关联也会影响报警。不能将至少一次命中解释为持续可靠报警或小车部署准确率。

## 环境与限制

GPU：NVIDIA GeForce RTX 4060 Laptop GPU；2.9.1+cu126；8.4.160；实际设备/类型：cuda:0 / torch.float32。
批量推理、读图与输出转CPU合计 53.59 秒，吞吐 219.4 FPS，不含加载、暖机、截图与视频编码；不是小车实时帧率。
该社区权重训练集划分和与URFD的重叠情况不明确，不能宣称独立泛化成绩。
证据在 evidence/，每段至少一张；两段本地视频仅为无逐帧人工标注的演示检查，不纳入准确率分母。

## 复现

在仓库根目录使用 .venv-demo/Scripts/python.exe demo/eval_yolo11_fall.py all --scope all --skip-videos；自动复用已验证缓存，仅推理缺失片段。
evaluate 只重放缓存和生成报告；videos 只输出两段演示视频。
