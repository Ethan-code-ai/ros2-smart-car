# YOLOE‑11s 搜索、杂物识别及雷达优化交接

2026-10-28。用户已明确小车暂不在场，本轮仅交付本地程序与安装包，不部署、不执行实车运动。此文档替代“已发送车端并验收”的任何推断。

## 已实现的程序

- YOLOE‑11s 独立推理进程，读取共享摄像头的最新 JPEG；队列容量为 1，默认 640、5 Hz。搜索及杂物共用词表，文本特征按词表缓存，旧任务/旧词表/过期画面不触发搜索命中。
- 自动巡航搜索与已有地图地点巡逻搜索。中文预设可展开，未知中文必须填写英文。默认置信度 0.45，最近五个新帧三次命中同一对象、跨度至少 0.4 秒后停车。
- 控制器搜索租约：控制器实例、任务和递增版本共同确认，心跳超过 1 秒锁定停车；过期心跳不能重新放行。搜索命中先锁定输出、停止模式，再暂停巡逻并保存证据，静止持续 0.5 秒才显示已停车。
- 杂物词表默认 bottle/cardboard box/bag/backpack/chair。没有相机/雷达标定只能标注物品，不报已确认占道；投影关联要求同步差不超过 0.15 秒，并持续 2 秒。报警不改变运动。
- 自动巡航摄像头归 Web 服务统一采集，既有 YOLO11 通过本机 `/api/video?frame=true&raw=true` 消费原始共享 JPEG，不再直接抢占 USB。人物/颜色跟随仍使用原所有权逻辑。
- 雷达控制入口不再使用旧的连续小缺束补空；增加严格时间戳/索引匹配的来源证据接口。几何模型、SLAM 和 TF 保持原配置，诊断工具同步关闭旧补空。
- 绕行增加短时差速弧线候选、角速度变化约束和近航点曲率限速，防止以过大转弯半径绕圈；保留 0.25 m/s、80% 意图速度、0.25 rad/s 上限，限时 45 秒/1.5 米。

## 运行前必须补齐的事项

尚未下载或在板端加载新权重，未测 GPU 内存、帧率、相机与雷达标定、实车搜索或绕行。没有板端联合性能证书时，`mobile_ready=false`，开始搜索接口拒绝运动；这不是故障时自动降低验收标准的开关。

旧版车端没有新增接口时，桌面显示车端不支持。安装 Windows 包不会自动部署 ROS 程序或 YOLOE 权重；桌面内的原车端启动入口保持原样，不伪造新部署。

### 模型准备

在车端或受控的兼容环境安装 `requirements-vision.txt`，保留 JetPack 对应的 PyTorch/CUDA，禁止通过通用 pip 命令盲目覆盖 NVIDIA 组件。运行：

```text
python scripts/prepare_yoloe11.py --directory ~/.ros/insightrover/models
```

该脚本显式联网准备 `yoloe-11s-seg.pt`、`mobileclip_blt.ts` 并记录哈希和依赖；要求至少 2 GiB 空间，不自动清理。正式推理关闭自动安装和联网下载，缺资源时报告错误。

服务配置为 `~/.ros/insightrover/vision.json`，默认模型目录同上；任务记录为 `search.json`，证据位于 `vision-evidence/` 与 `vision-events.sqlite3`。进程重启不会恢复运动。修改设置必须为停止模式、有新鲜里程计且线速度模与角速度均满足静止条件。

### 相机—雷达标定

由实际标定写入配置 `calibration`，不使用猜测的安装高度：

```json
{
  "image_size": [960, 540],
  "intrinsics": [800, 800, 480, 270],
  "base_to_camera": [0, -1, 0, 0, 0, 0, -1, 0, 1, 0, 0, 0]
}
```

以上数字仅解释结构，不能作为实车配置。四个内参依次为 fx/fy/cx/cy；十二个外参为 base_link 到光学相机坐标的 3×4 变换。尺寸须匹配共享 JPEG 实际分辨率，不能直接使用缩放前相机内参。

### 雷达驱动接入

`scripts/prepare_scan_provenance_patch.py` 接收私有驱动源文件，生成新文件，不覆盖厂家源码；源结构变化会拒绝生成。为私有包补上 std_msgs 的 CMake/package.xml 依赖，编译后记录原源文件、补丁和二进制哈希。

```text
python scripts/prepare_scan_provenance_patch.py --source /private/src/sllidar_node.cpp --output /private/review/sllidar_node.cpp
```

生成器在角度补偿之前记录实际角度采样，区分命中、补偿未采样与 SDK 零距离未知；在扫描发布前发送 `/scan/provenance`（std_msgs/String）。当前 SDK 的零距离没有充分语义依据，所以生成器不会输出 `no_return`。不能承诺此变更已经让空旷地缺测可通行。

来源包字段为 `stamp:[sec,nanosec]`、`frame_id`、`driver_sha256`、`healthy`、逐射线 `kinds`。其中生成器的 driver_sha256 标识插桩前源码版本，并不冒充二进制哈希。控制器最多缓存四帧，要求与原扫描完全匹配。

只有后续取得设备无回波语义及现场对照证据，才能部署 `scan-no-return-profile.json`，填写 verified、driver_sha256、evidence_sha256、usable_range_m，并由适配驱动发布真实 `no_return`。无配置、失配、遮挡、未采样和原因未知仍保守处理。先完成左前白纸/空旷/纸箱对照，禁止直接将 inf 全部写成量程上限。

### 联合性能验收

板端完成模型加载及安全检查后，先由操作者独立运行已有地图巡逻和视频，再观察：

```text
python scripts/benchmark_vision_service.py --url http://127.0.0.1:8080 --output ~/.ros/insightrover/vision-performance.json
```

工具自身不启动运动。至少持续 600 秒，识别有效率至少 3 Hz、结果年龄 P95 ≤0.5 秒、导航和视频同时运行，控制超期计数无新增。证书绑定构建、输入尺寸及已加载模型哈希；不满足条件的记录不能开启移动搜索。640 不达标时可停车改为 416，并重新执行性能和识别准确性验收。

## 接口

GET `/api/vision/status`、GET/POST `/api/vision/settings`、GET `/api/vision/events`、GET `/api/search`、POST `/api/search/start`、POST `/api/search/action`。状态接口和 WebSocket 包含 vision、search、search_control；桌面代理已允许这些路径。POST 错误会保留明确的未验收/未静止/任务冲突提示。

搜索请求示例：

```json
{"targets":[{"name":"服务器机柜","prompt":"server rack"}],"travel":"auto"}
```

地图搜索使用 travel=patrol，附 patrol 对象，沿用已有任务的 map_key、calibration_matrix、calibration_drawing、control_session_id、waypoints；仅巡逻一轮。操作请求为 `{"run_id":"当前任务标识","action":"pause|continue|cancel|finish"}`。目标证据中的位置为车辆位置，不是目标定位结果。

## 本地交付边界

已完成相关 Python/接口/桌面代理回归 135 项、前端 108 项与生产构建；冻结程序通过两轮本机隔离启动、登录、代理、视频、WebSocket 和数据保留检查。报告位于 `output/vision-search-20261028/regression.xml` 和 `output/windows-app-20261028-v0310/acceptance-result.json`。未宣称全仓库、板端、真实安装升级或实车验收。

发布目录包含 Windows 安装器、车端源码与前端静态资源包、SHA256 清单及本说明；模型在车端单独准备，不混入 Windows 包或源码包。车端源码包保留 ament resource 标记；部署时将 static_dir 指向包内 `web-console/dist`，或将该目录的内容安装到现有包的 web/static，避免启动旧前端。

下一次接车后：核实源文件及实际入口 → 保留回退副本 → 部署源码并离线加载模型 → 静止检查 → 联合性能 → 两类搜索和左右绕行/堵塞/急停验收。每类目标至少十次出现、九次成功停车，另做十分钟干扰物测试无误停车。最终保持停止。
