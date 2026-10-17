# YOLO11 躺卧即时告警：本地运行

当前入口使用原通用 YOLO11 和新跌倒权重，直接对 `fallen` 报警，不使用飞桨，不等待2秒。旧 `person_anomaly_demo.py` 是历史规则入口。

打开 PowerShell：

```powershell
Set-Location 'D:\智能小车\ros2-smart-car'
```

本机已有 `.venv-demo` 和 GPU 依赖，直接运行倒地视频。默认开启声音，按 `q` 退出；窗口红色横幅表示当前帧检测为倒地。

窗口逐帧显示检测框、类别、置信度、帧号和视频时间。空格播放／暂停，`N` 下一帧，`P` 上一帧，`R` 回到缓存首帧，`Q` 或 Esc 退出。回看最近300帧的已处理画面不会重复推理或报警；视频结束后窗口保留，便于继续查看。加 `--start-paused` 可先停在第一帧。

```powershell
& '.\.venv-demo\Scripts\python.exe' '.\demo\yolo11_fall_demo.py' --source 'D:\智能小车\倒地.mp4' --save-video --start-paused
```

运行正常视频和摄像头：

```powershell
& '.\.venv-demo\Scripts\python.exe' '.\demo\yolo11_fall_demo.py' --source 'D:\智能小车\正常.mp4' --save-video
& '.\.venv-demo\Scripts\python.exe' '.\demo\yolo11_fall_demo.py' --source 0
```

后台测试可加 `--headless --mute`。每次结果保存在独立的 `demo_output/yolo11-fall/<运行时间>/`。指定 `--output-dir` 时请使用一个新的空目录，防止覆盖旧证据。

- `environment.json`：两套权重、哈希和 GPU 信息。
- `frames.jsonl`：逐帧通用模型、跌倒模型输出以及当前告警状态。
- `events.jsonl`：`person_lying` 场景事件和首次命中的视频时间、截图路径。
- `evidence/`：每次告警开始时的原始画面标注截图。
- `summary.json`：总帧数、告警帧数、首次告警时间、实际推理设备。
- `annotated.mp4`：加 `--save-video` 后输出，蓝框为原通用模型，绿框为新模型其他姿态，红框为 `fallen`。

声音通过 Windows 系统提示音异步播放。持续命中不逐帧重复播放；首次命中立即请求播放，检测消失后再出现立即重报。系统静音或无音频输出设备会影响实际听到的声音。

仅重放全量缓存、生成即时告警报告（无需 GPU 推理）：

```powershell
& '.\.venv-demo\Scripts\python.exe' '.\demo\eval_yolo11_fall.py' evaluate
```

报告在 `demo/results-yolo11-fall-all-immediate/report.md`，历史2秒结果仍在原目录。数据集评测与实际 demo 共用相同的 `fallen` 告警判据。

现有全量基准：真实跌倒28/30、全部躺卧40/46、普通活动误报12/23。这是本地数据上的片段统计，取消确认时间不会修复模型误分类，也不代表小车实机准确率。
