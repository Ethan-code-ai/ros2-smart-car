# YOLO11 Human Fall Detection：飞桨失败子集测试

日期：2026-10-16。按用户要求仅运行飞桨失败片段，不运行全集、不训练模型、不修改原模型及旧结果。

## 输入与计分

- 权重：`D:\智能小车\ros2-smart-car\ros2_ws\src\smart_car_decision\models\yolo11-fall.pt`。
- 固定参数：640×640，conf=0.25，IoU=0.7，FP32，CUDA，批量8；没有阈值搜索。
- 权重类别为 `fallen`、`sitting`、`standing`。直接按 `fallen` 计分，不使用原人物过滤和宽高比规则。
- 以全部躺卧为正：14段飞桨漏掉的真实跌倒、4段飞桨漏掉的主动躺卧，以及3段飞桨误报的普通活动，共21段、3030帧。
- 选择清单保存为 `D:\智能小车\ros2-smart-car\demo\results-yolo11-fall-paddle-failures\selection.json`，包含来源报告的SHA256。
- 原始分类和2秒确认分别计分。原始命中要求至少覆盖一个官方已标注躺卧帧；过渡和缺失标注不参与帧级混淆矩阵。短片不循环、不冻结，EOF不推进计时。
- 确认层使用全部三类框做独立IoU关联；每人累计2秒相邻有效躺卧观察，短缺测暂停，超过0.3秒重置候选，超过1秒无阳性释放告警。它只影响确认层，不会丢弃原始模型命中。
- 失败子集有选择偏差，比例不能当作全集准确率。权重训练数据是否包含URFD不明确。

## 复现

在PowerShell中运行：

```powershell
Set-Location 'D:\智能小车\ros2-smart-car'
$env:PYTHONIOENCODING = 'utf-8'

# 默认只选飞桨失败子集；已有且指纹相同的预测自动复用。
& '.\.venv-demo\Scripts\python.exe' '.\demo\eval_yolo11_fall.py' all

# 仅重放已有预测和生成报告，不使用GPU。
& '.\.venv-demo\Scripts\python.exe' '.\demo\eval_yolo11_fall.py' evaluate

# 仅生成本地“正常”“倒地”视频的标注输出。
& '.\.venv-demo\Scripts\python.exe' '.\demo\eval_yolo11_fall.py' videos

# 纯逻辑测试，不需要GPU。
& '.\.venv-demo\Scripts\python.exe' '.\tests\test_eval_yolo11_fall.py'
```

可用 `--sequences fall-19,fall-25` 进一步缩小范围；不接受不属于飞桨失败集的序列。`--force` 强制重新推理。GPU不可用会明确失败，不会自动退回CPU。

## 产物

结果目录：`D:\智能小车\ros2-smart-car\demo\results-yolo11-fall-paddle-failures`。

- `report.md`：中文报告和逐段结果。
- `metrics.json`：原始分类、2秒确认、帧级诊断与环境信息。
- `sequences.csv`、`selection.json`：片段结果和选取依据。
- `evidence`：每个失败样本的真实帧证据。
- `events`：2秒告警的开始与结束，结束原因可为视频结束。
- `videos`：两段标注视频、逐帧预测、视频摘要和截图；视频均重新解码检查输出帧数。
- `run-20261016.log`：本次GPU运行日志。旧版API对显式 `half=False` 的警告不影响本次FP32推理；入口已兼容此API，后续不再反复打印该警告。

原始推理缓存独立保存在 `D:\智能小车\ros2-smart-car\.urfall-work\yolo11-fall-paddle-failures`，可重复计分，不覆盖旧YOLO或飞桨缓存。
