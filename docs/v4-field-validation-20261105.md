# v4.0.0 现场验证记录 · 2026-11-05

本轮连接 192.168.1.106（yahboom）。用户确认平地、前方至少一米无遮挡、有人配合，并在最后一轮确认看到了前进和正常停车。地图导航及相关任务验收按用户要求暂缓。

## 部署与修复

车端使用独立目录 /home/jetson/insightrover-v4-649c57a1216c，服务 smart-car-v4.service 未启用开机自启。原 0.3.11 目录和校准数据保留。初始模式为 stop，无运动租约时最终输出为零。

源码包 SHA-256：649c57a1216c9220366bf6af5430a6f5ba605a2deda744fad7c7fa387972db1b。运行组件 source_sha256：ba9b1271d1bae7ba83a0fe0e275d6f267fdbd3a76d5bde61ec035e97ea7fa0eb。

首次启动发现控制租约 ROS 话题中的连字符不合法，导致两个节点退出。现改为 /robot/control_lease/request 和 /robot/control_lease/status，HTTP 路径保持 /api/control-lease，增加实际 ROS 名称检查，相关 28 项回归通过。

另确认厂家 rosmaster_main.py 独占底盘串口，第二个驱动因此异常。核实身份后退出厂家程序，再启动 v4，里程计恢复。实际车端入口和桌面启动脚本均增加冲突拒绝。未修改避障阈值。

视觉模型与隔离依赖已恢复到持久目录 /home/jetson/.local/share/insightrover/vision-v4，运行时禁止下载。系统 torch、OpenCV 保留，隔离安装 ultralytics 8.4.0、ftfy 6.3.1、regex 2024.11.6，CLIP 提交 7ffa84b3bfa40c42ecc1c77147a855e69cb2dd40。

- YOLOE-11s：8e439445c87338b79d9ce21dec109f4621e26df67e94d26ea1a98c1e64dce3e3。
- 文本编码器：a67804d1b0f07b8b9a20c1761ec0847f34660f5fa338ec70e8f3fce68ed95e54。

机器凭据保存在车端权限 0600 的私有环境文件及 Windows 专用凭据库中，证据不含凭据值。

## 实测结果

| 检查 | 本轮结果 |
|---|---|
| 静止启动 | 匿名请求被拒绝，雷达、里程计、TF 新鲜；实际 /cmd_vel 只有 decision_controller 发布，采样期间全为零 |
| 停车链路 | 控制器回执及停车后的新鲜里程计均确认静止 |
| 首轮前进 | 0.07 米/秒上限、1.8 秒窗口，里程计位移约 5.96 厘米 |
| 延长时间 | 同一速度上限、4.5 秒窗口，位移约 8.31 厘米；未据此修改底盘标定或安全参数 |
| 按距离停车 | 上限 0.10 米/秒、最长 4.5 秒，实时里程计达到 20 厘米即停止；最终约 20.58 厘米，现场确认前进及停车正常 |
| 共享业务入口 | 三轮前进均经过真实主机权限及共享驾驶入口；使用隔离验收数据库，未改生产业务记录 |
| 租约过期 | 保持 stop 模式且不续约，三秒后代际失效；迟到续约返回 409，全程无运动 |
| 搜索可靠性 | 先取消后派发保持 cancelled，重复启动返回同一 run_id |
| 静止搜索 | 一次瓶子目标进入 found；采样期间全为零，最终里程计确认静止 |

证据位于 output/v4-vehicle-preflight：stationary-preflight.json、low-speed-20261105-173326/evidence.json、stationary-protocols-20261105-173637/evidence.json。前进证据已记录用户现场确认。

## 交付及边界

新候选为 output/windows-app-v4-field-candidate-20261105/installer/InsightRover-Setup-4.0.0-x64.exe，SHA-256：6e58cc0c865fdfe4a881e4c0e3869fc5a4ce250fb5f3c8fa3b8d77dfeffc8443。冻结程序两轮隔离启动、登录、HTTP、视频、WebSocket、升级备份和系统凭据检查通过；包内未发现当前配置的模型密钥。未覆盖本机已安装的 0.3.11，安装器实际安装、升级、卸载尚未测试。

本轮没有调用模型 API，沿用用户指定的十条典型场景结果。

尚未完成移动中的失联/急停、左右绕障及双侧堵塞、逐目标十次识别、600 秒干扰和导航/视觉/视频联合验收。一次静止搜索不等于完整能力验收；手动前进不等于导航验收。

未登记完整能力验收记录。导航、巡检、运输及路线搜索的实车验收暂缓，相关业务入口继续按原规则禁用。
