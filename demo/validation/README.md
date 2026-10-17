# 双模型即时躺卧告警：本地运行证据

本目录归档 `demo/yolo11_fall_demo.py` 在本机真实执行两套模型的结果：同一原始画面分别进入通用 `yolo11s.pt` 与社区预训练 `yolo11-fall.pt`，跌倒分支的 `fallen` 单帧置信度达到 0.25 即告警，不等待 2 秒，也不依赖通用分支先检出人员。本轮未训练模型，未加载飞桨。

归档从 `demo_output/yolo11-fall/validation-immediate-fall/` 和 `validation-immediate-normal/` 完整复制，原件保留；12 个文件逐一校验 SHA256 相同。原始 JSON/JSONL 中的绝对路径保留为历史运行记录，浏览归档请使用下面的相对链接。

## 运行材料与结果

素材为原本机 `D:\智能小车\倒地.mp4` 和 `D:\智能小车\正常.mp4`，没有逐帧人工标注。下面的视频是实际双模型运行生成的带标注输出，不计入数据集准确率。

| 本地素材 | 输入及输出帧数 | 告警帧 | 首次告警 | 运行记录与输出 |
| --- | ---: | ---: | ---: | --- |
| 倒地.mp4 | 108 | 84 | 0.200 秒 | [视频](validation-immediate-fall/annotated.mp4) · [总结](validation-immediate-fall/summary.json) · [逐帧记录](validation-immediate-fall/frames.jsonl) · [事件](validation-immediate-fall/events.jsonl) |
| 正常.mp4 | 139 | 0 | 无 | [视频](validation-immediate-normal/annotated.mp4) · [总结](validation-immediate-normal/summary.json) · [逐帧记录](validation-immediate-normal/frames.jsonl) · [事件](validation-immediate-normal/events.jsonl) |

[首次告警截图](validation-immediate-fall/evidence/lying-000007.jpg) 来自第 7 帧，视频时间为 0.200 秒；另有 [后续告警截图](validation-immediate-fall/evidence/lying-000025.jpg)。首次截图在通用分支推理前保存，完整双模型框请查看对应视频。正常素材事件文件为空，与没有告警一致。

## 权重与运行环境

两次运行分别保存完整环境和推理参数：[倒地素材环境](validation-immediate-fall/environment.json)、[正常素材环境](validation-immediate-normal/environment.json)。

| 项目 | 记录值 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU |
| Python / PyTorch | 3.13.5 / 2.9.1+cu126 |
| Ultralytics / CUDA | 8.4.160 / 12.6 |
| 推理 | `cuda:0`，FP32，640 输入，batch=1 |
| 跌倒分支 | `fallen / sitting / standing`，conf=0.25，IoU=0.7 |
| 通用分支 | YOLO11s 80 类，conf=0.35，IoU=0.7 |

| 权重 | SHA256 |
| --- | --- |
| [`yolo11-fall.pt`](../../ros2_ws/src/smart_car_decision/models/yolo11-fall.pt) | `3f56ad30358d5c63bf8dbc0c1299cf68818c3d291dfb10c94107b94110aadd4c` |
| [`yolo11s.pt`](../../ros2_ws/src/smart_car_decision/models/yolo11s.pt) | `85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5` |

通用权重的原文档记录了 [Ultralytics YOLO11 仓库](https://huggingface.co/Ultralytics/YOLO11) 和 [yolo11s.pt 文件页](https://huggingface.co/Ultralytics/YOLO11/blob/main/yolo11s.pt)。现有本地源码和文档没有记录 `yolo11-fall.pt` 的原始下载 URL，因此仅标为社区预训练权重；其训练集与 URFD 的重叠情况未知。

## 验证范围

- 两段输出视频在原验证中重新解码，分别为 108 与 139 帧，和输入帧数一致；本次归档保持原文件内容。
- 本轮使用 `--headless --mute`，没有听觉验证扬声器实际发声。
- 这是电脑本地视频 demo，尚未将该即时告警分支集成到小车 ROS2 链路，也未完成车载低视角实机验证。
- 首次截图中的 `fallen` 框主要覆盖部分腿脚，画面实际为床上躺卧；触发告警不代表完整人体定位成功，也不能直接证明发生了跌倒。
- 两段无逐帧标注的演示视频不能用于计算准确率或泛化性能。完整本地数据集的口径、误报与漏报见 [即时策略评测报告](../results-yolo11-fall-all-immediate/report.md)。

运行步骤见 [本地运行说明](../../docs/YOLO11躺卧即时告警-本地运行.md)。
