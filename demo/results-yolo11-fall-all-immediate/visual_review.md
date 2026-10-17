# 本轮人工视觉检查

已查看新双模型 demo [首次告警截图](../validation/validation-immediate-fall/evidence/lying-000007.jpg)；真实运行证据现归档于 [demo/validation](../validation/README.md)，原始文件仍保留在 `demo_output/yolo11-fall/validation-immediate-fall/`。

- 截图为真实输入视频第7帧，横幅显示即时告警、视频时间0.200秒，与事件记录一致。
- `fallen` 框及约0.254置信度可见。模型此帧只框住部分腿脚，不能把该框解释为完整人体定位成功；画面实际为床上躺卧。
- 首次证据在通用模型推理前保存，以免等待通用分支。包含两套框的 [完整双模型视频](../validation/validation-immediate-fall/annotated.mp4) 已一并归档。
- 自动检查109张数据集证据均可读取；两段实际双模型输出视频重新解码分别139和108帧。

本轮测试静音，未确认扬声器实际发声。
