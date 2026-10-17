# 人员异常检测 Demo

> 当前躺卧告警请使用新增的 `yolo11_fall_demo.py`：保留原通用模型，新加 YOLO11 跌倒专用模型，`fallen` 立即报警，不使用飞桨、不等待2秒。[本地运行步骤](../docs/YOLO11躺卧即时告警-本地运行.md)。下文是旧规则 demo 的历史说明。

这是梁家齐负责的第一版独立演示程序。它只处理人员相关异常：疑似倒地、长时间停留和人员持续占用通道，不接烟雾传感器，也不修改 ROS2 或 Web 控制台。通道占用默认由建图后的地图层提供信号；单独播放视频时不会把画面中央的人自动当成通道占用。

## 完整测试流程

下面的命令都在项目根目录执行。先打开 PowerShell，并进入仓库目录：

```powershell
Set-Location 'D:\智能小车\ros2-smart-car'
```

### 1. 建立环境并安装依赖

本机已经验证 Python 3.13 可以运行。如果另一台电脑安装失败，改用 Python 3.12：

```powershell
py -3.13 -m venv .venv-demo
# 如果上面的 Python 版本不可用，改为：py -3.12 -m venv .venv-demo
\.venv-demo\Scripts\python.exe -m pip install --upgrade pip
\.venv-demo\Scripts\python.exe -m pip install -r .\ros2_ws\src\smart_car_decision\requirements-vision.txt
```

### 2. 检查代码和配置

先检查 Python 文件是否能编译，再读取配置文件：

```powershell
\.venv-demo\Scripts\python.exe -m py_compile .\demo\anomaly_rules.py .\demo\person_anomaly_demo.py .\tests\test_person_anomaly_demo.py
\.venv-demo\Scripts\python.exe -c "import json; from demo.anomaly_rules import AnomalyConfig; print(AnomalyConfig.from_mapping(json.load(open('demo/person_anomaly_config.json', encoding='utf-8'))))"
```

### 3. 运行自动测试

```powershell
\.venv-demo\Scripts\python.exe .\tests\test_person_anomaly_demo.py
```

看到下面的结果，就表示倒地、停留、通道堵塞、短时丢失和告警优先级等纯判断逻辑通过：

```text
Ran 7 tests
OK
```

### 4. 用录制视频进行正式演示

把测试视频放在仓库根目录，例如命名为 `demo_video.mp4`，然后运行：

```powershell
\.venv-demo\Scripts\python.exe .\demo\person_anomaly_demo.py --source .\demo_video.mp4 --show-fps
```

建议按下面顺序安排视频内容：

1. 人员正常走动，确认画面有人框但没有异常告警。
2. 人员进入画面侧方的停留区域，基本不动 8 秒，画面显示“长时间停留”。
3. 接入小车和地图监视器后，原本可通行的地图通道出现持续阻塞，画面显示“通道堵塞”。
4. 人员在安全垫上侧躺并保持 1.5 秒，画面显示“疑似倒地”。
5. 人员离开画面，确认短时间消失不会立即清除计时，超过 1 秒后事件结束。
6. 按 `r` 清除当前计时和告警；按 `q` 退出程序。

正式汇报优先使用录制视频，现场光线或摄像头角度变化较小时再使用 USB 摄像头。

### 5. 使用 USB 摄像头演示

默认摄像头：

```powershell
\.venv-demo\Scripts\python.exe .\demo\person_anomaly_demo.py --source 0 --show-fps
```

如果默认摄像头打不开，可尝试第二个设备编号：

```powershell
\.venv-demo\Scripts\python.exe .\demo\person_anomaly_demo.py --source 1 --show-fps
```

### 6. 检查事件记录和截图

退出程序后，在项目根目录运行：

```powershell
Get-ChildItem .\demo_output -Recurse
Get-Content .\demo_output\events.jsonl
```

正常情况下会看到：

```text
demo_output/
├─ events.jsonl
└─ evidence/
   └─ <event_id>.jpg
```

`events.jsonl` 每行是一条 JSON 事件，包含事件编号、异常类型、开始或结束状态、人员编号、时间、持续时长、识别可信度、人员位置、视频来源和截图路径。异常开始时保存一张截图，结束事件会记录结束时间和持续时长，但不会重复保存截图。

### 7. 按现场情况调整参数

```powershell
notepad .\demo\person_anomaly_config.json
```

常用参数如下：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `fall_ratio` | `0.9` | 人员框宽高比达到该值才进入倒地计时（原 1.1，按 URFD 实测下调） |
| `fall_seconds` | `0.5` | 倒地状态持续多久才报警（原 1.5，原值导致一次都报不出来） |
| `fall_feature_mode` | `bbox` | 倒地判据：`bbox` 宽高比 / `angle` 关键点躯干角 / `auto` 角度优先回退宽高比 / `either` / `both`。实测 `bbox` 最好，见 9.4 |
| `fall_angle_degrees` | `60.0` | 躯干角阈值，仅在非 `bbox` 模式生效 |
| `keypoint_confidence` | `0.3` | 关键点可信度门限，低于此值的关节不参与判断 |
| `track_confidence` | `0.15` | 已确认轨迹可以靠这么弱的检测续命；新轨迹仍需 `confidence` |
| `linger_seconds` | `8.0` | 停留区域内持续多久才报警 |
| `block_seconds` | `4.0` | 通道持续占用多久才报警 |
| `track_lost_grace_seconds` | `1.0` | 人员短暂消失时保留状态的时间 |
| `corridor_overlap_ratio` | `0.25` | 人员框与通道区域的最小重合比例 |
| `corridor_source` | `map` | `map` 使用建图后的占用信号；`image_zone` 才使用画面矩形规则 |
| `linger_zone` | 见配置文件 | 停留区域矩形，格式为 `[左, 上, 右, 下]` |
| `corridor_zone` | 见配置文件 | 兼容 `image_zone` 模式的通道矩形，格式为 `[左, 上, 右, 下]` |

### 7.1 接入建图后的通道占用信号

小车应先在实验室内完成一圈建图，并冻结一份基准地图。地图监视器再把当前激光扫描与基准地图比较：只有原本可通行的通道出现持续障碍时，才将 `corridor_occupied=True` 传给异常引擎。视频 demo 本身没有机器人位姿、激光雷达和地图坐标，不能从单个视频恢复这个判断。

调用异常引擎时传入地图层结果：

```python
events, snapshots = engine.update(
    detections,
    frame_width=frame_width,
    frame_height=frame_height,
    now=now,
    corridor_occupied=map_monitor.corridor_blocked,
)
```

如果只是需要演示旧的画面矩形规则，显式把配置改成 `"corridor_source": "image_zone"`；这不代表真实小车的地图通道占用判断。

修改后重新运行第 4 步即可生效。也可以手动指定模型、跟踪配置和参数文件：

```powershell
\.venv-demo\Scripts\python.exe .\demo\person_anomaly_demo.py `
  --source .\demo_video.mp4 `
  --model .\ros2_ws\src\smart_car_decision\models\yolo11s-pose.pt `
  --tracker .\ros2_ws\src\smart_car_decision\config\bytetrack.yaml `
  --config .\demo\person_anomaly_config.json `
  --show-fps
```

> `--model` 的默认值已改为 **`yolo11s-pose.pt`**：它对躺在地上的人检出率是 `yolo11s.pt` 的两倍以上（71.7% vs 32.3%，见第 9 节），这是决定倒地能不能报出来的关键。站立行人的检出率两者都是 ~100%，所以停留和通道占用判断不受影响。

### 8. 完成验收的标准

- 自动测试输出 `Ran 7 tests` 和 `OK`；
- 正常走动时不产生异常事件；
- 停留达到 8 秒只产生一条“长时间停留”开始事件；
- 地图层把通道标记为占用并持续 4 秒才产生一条“通道堵塞”开始事件；
- 横向姿态达到 1.5 秒只产生一条“疑似倒地”开始事件；
- 同一个人同时满足多项条件时，画面按“倒地、堵塞、停留”显示最高优先级；
- 异常首次出现时 `evidence/` 有对应截图；
- 退出后 `events.jsonl` 可读，开始和结束事件的人员编号、时间和持续时长合理。

这是一版流程 Demo。倒地判断目前使用人员框的形状和持续时间，后续应增加人体关键位置识别，并用更多真实视频验证准确率。

## 9. 在公开数据集上量化准确率

> 完整的评测与改进过程记录（做过什么、测出什么、哪些假设被证伪、下一步做什么）见
> **[`docs/倒地检测-评测与改进工作日志.md`](../docs/倒地检测-评测与改进工作日志.md)**。
> 本节只讲怎么跑；结论和取舍理由在那篇日志里。

`demo/eval_urfall.py` 是配套的离线评测程序，用 UR Fall Detection Dataset（URFD）的逐帧官方标注来量化倒地规则的准确率。它不开窗口、不需要摄像头，可以在 GPU 上批量跑完。

设计上把**昂贵的 GPU 推理**和**廉价的规则回放**拆开：推理只跑一遍并把每帧检测结果缓存成 JSON，之后的计分和阈值扫描是纯 Python，几秒就能重跑一遍。

### 9.1 准备 GPU 环境

`.venv-demo` 默认装的是 CPU 版 torch。换成 CUDA 版（Windows 的 PyTorch 轮子自带 CUDA 运行时，不需要另装 CUDA Toolkit）：

```powershell
.\.venv-demo\Scripts\python.exe -m pip freeze > .\.venv-demo-freeze-backup.txt
.\.venv-demo\Scripts\python.exe -m pip install `
  D:\wheelcache\torch-2.9.1+cu126-cp313-cp313-win_amd64.whl `
  torchvision==0.24.1+cu126 --index-url https://download.pytorch.org/whl/cu126
```

必须**一条命令同时装 torch 和 torchvision**：torchvision 的二进制与 torch 版本强绑定，单独降级 torch 会让 torchvision 在 import 时报错。回滚按 `.venv-demo-freeze-backup.txt` 重装即可。

验证：

```powershell
.\.venv-demo\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### 9.2 运行评测

```powershell
# 自测（不需要 GPU / 数据集 / 网络）
.\.venv-demo\Scripts\python.exe .\tests\test_eval_urfall.py

# 解压数据集 + 下载官方逐帧标注
.\.venv-demo\Scripts\python.exe .\demo\eval_urfall.py prepare --dataset-dir 'D:\智能小车\dataset'

# GPU 推理一次，结果缓存到 .urfall-work\detections\
.\.venv-demo\Scripts\python.exe .\demo\eval_urfall.py detect --device cuda:0

# 计分 + 阈值扫描 + 留一法交叉验证（纯 CPU，可反复跑）
.\.venv-demo\Scripts\python.exe .\demo\eval_urfall.py evaluate --sweep --loso

# CPU 对照基准
.\.venv-demo\Scripts\python.exe .\demo\eval_urfall.py bench --device cpu --sequences fall-04

# 五种倒地判据的 A/B 对比（宽高比 / 躯干角 / 组合）
.\.venv-demo\Scripts\python.exe .\demo\urfall\compare.py --loso

# 生成改进前后的对比截图
.\.venv-demo\Scripts\python.exe .\demo\urfall\evidence.py

# 事件级指标 + 帧级漏检按后果拆解（报警前延迟 vs 报警后掉线）
.\.venv-demo\Scripts\python.exe .\demo\urfall\event_metrics.py
```

`evidence.py` 需要两份缓存才能左右对比。生成旧模型那一份：

```powershell
Move-Item .\.urfall-work\detections .\.urfall-work\detections-pose
.\.venv-demo\Scripts\python.exe .\demo\eval_urfall.py detect --device cuda:0 `
  --model .\ros2_ws\src\smart_car_decision\models\yolo11s.pt
Move-Item .\.urfall-work\detections .\.urfall-work\detections-boxmodel
Move-Item .\.urfall-work\detections-pose .\.urfall-work\detections
```

产物在 `demo/results-urfall/`：`report.md`（中文报告）、`metrics.json`、`sweep.csv`、`events/<序列>.jsonl`、`failures/*.jpg`（失败帧截图）。中间产物在 `.urfall-work/`，已加入 `.gitignore`。

单独查看某一段的逐帧情况（检出、track ID、宽高比对照真实标注），这是定位问题最快的方式：

```powershell
.\.venv-demo\Scripts\python.exe .\demo\urfall\diagnose.py fall-04
```

### 9.3 改进前后对比

> **本节数字来自最初的 10 段子集。数据集扩充到 69 段后结论显著改变，以 9.6 节为准。**

在 URFD 的 10 段（5 段跌倒 / 5 段日常活动、1386 个可评测帧）上实测：

| | 改进前 | 改进后 |
|---|---:|---:|
| 检测模型 | yolo11s | **yolo11s-pose** |
| 倒地判据 | 宽高比 ≥ 1.1 | 宽高比 ≥ **0.9** |
| 去抖时长 | 1.5 s | **0.5 s** |
| `track_buffer` | 8 (0.27 s) | **30 (1.0 s)** |
| 躺地帧人员检出率 | 32.3% | **71.7%** |
| **事件级：跌倒报出** | **0/5** | **4/5** |
| **事件级：日常活动误报** | 0/5 段 | **0/5 段** |
| **告警延迟** | — | **中位 17 帧 / 最大 18 帧（0.60 s）** |
| **报警后覆盖率** | — | **100%**（报警拉起后不掉线） |
| 帧级 TP / FP | 0 / 0 | 129 / 0 |
| 帧级精确率 | 未定义（无正预测） | 100% |
| 帧级召回率（含去抖窗口） | 0.0% | 57.8% |
| 单段多 track ID | 3 段 | **1 段** |

> **主指标是事件级，不是帧级。** 巡逻场景每次跌倒报一次就够，不需要在人躺着的每一帧都持续报警；帧级召回率把去抖窗口算作漏检，会系统性低估实际表现。实测 94 个帧级漏检**全部**发生在首次告警之前，**报警后掉线 0 帧**——告警一旦拉起就 100% 保持到最后。唯一真正的失败是 fall-01 这一次漏报。

截图证据在 `demo/results-urfall/evidence/`（左右对比，同一帧、同一刻），失败帧在 `demo/results-urfall/failures/`。

### 9.4 三个值得记录的实测结论

**① 瓶颈是检测，不是阈值。** 改进前默认参数一次告警都没触发（TP=0）。根因不是阈值设错，而是 YOLO11s 对站立的人检出率 99.7%、对躺在地上的人只有 32.3%——没有人员框，宽高比规则无从判断。换 `yolo11s-pose` 把躺地检出率提到 65.9%（配合两级置信门限后 71.7%），这一项贡献了绝大部分收益。

**② 躯干角没有赢过宽高比——和预期相反。** 用 17 个 COCO 关键点算肩中点→髋中点的倾角，理论上更抗截断和视角变化。实测在 URFD 上**明显更差**（`demo/urfall/compare.py --loso`，五种判据在同一批缓存检测上调优后对比）：

| 判据 | 调优后 F1 | 留一法样本外 F1 |
|---|---:|---:|
| **宽高比** | **0.791** | **0.779** |
| 任一满足 | 0.758 | 0.747 |
| 同时满足 | 0.632 | 0.632 |
| 躯干角优先 | 0.142 | 0.042 |
| 躯干角（无回退） | 找不到零误报配置 | — |

两个原因：**覆盖更低**（223 个躺地帧里只有 91 帧能同时拿到可信的肩和髋，而有外框的是 147 帧）；**透视缩短**（人躺着时身体长轴常朝向摄像头，肩髋连线在画面里又短又接近竖直，而四肢摊开仍让外框变宽）。所以默认 `fall_feature_mode` 保持 `bbox`。关键点能力已经保留在代码里，换到低视角的小车摄像头后值得重测——那里外框透视畸变更严重，结论可能反转。

**③ 两级置信门限提升了检出覆盖，但没改变告警结果。** `track_confidence=0.15` 让已确认的轨迹靠弱检测续命，躺地帧检出率从 65.9% 升到 71.7%，但消融实验显示它、冷却时长、丢失宽限三者对最终 TP/FP **一个都没变**。保留是因为零成本且更稳健，不是因为在这批数据上有效。

性能：RTX 4060 Laptop 上 yolo11s-pose 纯模型 96–127 fps、显存峰值 114 MB，远超 30 fps 实时需求。

### 9.5 仍未解决的

- **机位未标定**：URFD 是墙上约 1 m 的固定机位，小车摄像头离地很近，透视关系完全不同。上面所有阈值都必须在实际小车上重新采数据标定，不能直接外推。

### 9.6 全量数据集（69 段）结果 —— 结论被推翻

数据集扩充到 **69 段 / 11756 帧 / 9654 个可评测帧 / 30 个跌倒事件**（原来是 10 段、5 个跌倒）后，10 段子集上的乐观结论不成立：

| | 10 段子集 | **69 段全量** |
|---|---:|---:|
| 跌倒报出 | 4/5（80%） | **16/30（53%）** |
| 误报段数 | 0/5 | **14/39** |
| 误报帧 | 0 | **855 帧（175.7 帧/分钟）** |
| 帧级精确率 | 100% | **39.6%** |
| 交叉验证样本外 F1 | 0.779 | **0.320** |

**根本原因：URFD 的日常活动段里有 16 段是人「主动躺在地板上」，终止姿态与真跌倒完全相同。** 规则在其中 **13 段误报**。而普通日常活动段（无躺下动作）23 段里只误报 1 段——说明问题不是阈值松，而是**静态姿态判据原理上无法区分「摔倒」和「自己躺下」**。

证据：`evidence/混淆-fall-08f078-vs-adl-31f146.jpg` 等三张左右对比图。左边真跌倒 `ratio=1.85 角度=83°`，右边主动躺下 `ratio=1.32 角度=75°`，两者数值与画面都几乎一样，规则都报了警。

这也解释了为什么「零误报」调参会塌掉：推荐参数被推到扫描上界 `fall_ratio=1.8`，召回只剩 17.9%——想不误报就只能把阈值调到几乎不报警。

**两个计分口径**（报告第 4 节同时给出）：
- `fall_only`（主口径）：只有跌倒段的躺地帧算正样本，日常段主动躺下算负样本，在那报警 = 误报。
- `any_lying`：只问「能不能看出有人躺着」，不问是不是跌倒。两者的差距就是区分不开的代价。

**结论：要真正可用，必须引入运动动力学**——跌倒是「快速下降 + 落地后不起」，主动躺下是缓慢受控的。可参考 mfarchan-arm 的状态机（髋部下降速度 > 0.7 身高/秒、躯干角速度 > 120°/秒），单靠任何静态姿态特征都做不到。

#### 两个在扩充后依然成立的结论

- **报警后掉线 0 帧**，报警后覆盖率仍是 100%：告警一旦拉起就稳定保持。
- **跌倒段的 414 个帧级漏检全部是报警前延迟**，没有一帧是中途掉线。告警延迟中位 9 帧、最大 20 帧（0.67 秒）。

#### 评测框架在这轮修掉的两个缺陷

1. `event_level` 原先只把「没有躺地标注」的 ADL 段计入误报，导致 16 段难负样本被豁免，**系统性低估误报**（报告 1/39 段，实际 14/39 段）。
2. 交叉验证原为逐段留一法，69 段时要跑 69 次全量扫描。改为分组 k 折（`--loso-folds`，默认 10），序列轮流分配保证每折 fall/adl 混合。
