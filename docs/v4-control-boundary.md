# v4 共享控制边界实施记录

业务 `/robots/{id}/control`、`/robots/{id}/emergency-stop` 与兼容写代理都检查当前物理车辆绑定。数据库中存在机器人记录不代表它对应当前车端地址；非绑定车辆返回 409，且不会联系车端。

通用业务控制不再允许 `patrol_task` 和 `mission_task` 指令绕过持久化任务入口。进入 mapping 模式除了 `robots.control` 还需要 `maps.manage`。停止请求不要求遥测在线；发送失败不能改变已观测状态或冒充停车成功。

兼容写代理不作为 OpenAPI 业务操作公开，其路径仍由白名单和权限检查约束。未知写路径在登录检查后返回 404。

地图保存和加载也要求 `robots.control` 与 `maps.manage`，通过同一实车绑定取得网关地址。

车端 HTTP/WebSocket 已接入机器凭据验证；仅 `/health` 不需要凭据。主机与车端必须分别配置相同的 `ROBOT_SERVICE_TOKEN`（至少 32 个字符，应使用随机生成的秘密值），通过 `X-InsightRover-Service-Token` 请求头传递。用户 JWT 不可替代机器凭据；不把凭据放到 URL 或前端配置。未配置或过短时车端 HTTP 返回 503，错误凭据返回 401，WebSocket 拒绝连接。主机 RobotGateway 和兼容写入口携带此凭据。

主前端的状态、地图及单帧视频通过带 Bearer 的 `/api/control/...` 读取。主机只转发允许的路径和必要查询参数，不转发用户凭据给车端。状态 WebSocket `/ws/status` 在连接后五秒内要求 `{type:"authenticate",token:"用户访问令牌"}`，令牌不放 URL；转发期间重复校验权限、令牌有效性及车辆绑定。桌面裸代理已移除，Vite 使用业务后端作为代理目标。

桌面凭据已接入 Windows 系统凭据存储，主机与车端共享控制代际和租约。手动驾驶、SLAM 建图、独立导航及助手任务均通过授权控制入口。实车验收记录按构建、地图和模型绑定，撤销与版本变化会关闭相应的新任务入口。

已完成的模拟回归和浏览器检查见 v4-browser-acceptance.md。真实视频联合负载、故障停车及各任务的物理验收仍待完成，登记要求见 v4-physical-acceptance.md。遗留 RosConsoleApp 的旧 MJPEG 面板尚未迁移。
