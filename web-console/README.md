# ROS2 Smart Car Web Console

React 19 + TypeScript + Vite 实现的 ROS2 智能小车控制台。当前已通过 REST 与 WebSocket 连接 FastAPI/ROS2 后端，支持模式、急停、手动方向与速度、HSV 配置、人物目标选择、MJPEG 视频、雷达、地图和系统健康状态。项目总体完成情况与实车边界见[仓库首页](../README.md)及[当前进展](../docs/项目进展总览.md)。

## 运行

```bash
cd web-console
npm ci
npm run dev
```

默认访问：

```text
http://127.0.0.1:5173/
```

前端请求使用同源 `/api/*`、`/ws/*` 和 `/video_feed`；当前 Vite 配置没有后端代理。仅运行开发服务器可以查看页面结构，但需要同源后端或自行配置开发代理才能控制机器人和获取实时数据。

生产构建：

```bash
npm run build
```

在 Jetson 上由 ROS2 `web_app_node` 提供 `dist` 静态资源与接口，默认端口 8080。部署前将 `decision.yaml` 中 `web_app_node.static_dir` 配置为当前机器的实际 `web-console/dist` 绝对路径。

## 主要文件

```text
src/App.tsx                         页面状态和整体布局
src/App.css                         深色 HUD、玻璃拟态、雷达和响应式样式
src/useRobotState.ts                状态快照、WebSocket 重连与控制操作
src/robotApi.ts                     REST 请求、状态类型及默认值
src/data/consoleData.ts             模式和状态卡片定义
src/colorPersistence.ts             浏览器颜色配置保存与恢复
src/components/Sidebar.tsx          左侧模式导航与 WebSocket 状态
src/components/HeaderBar.tsx        顶部标题与急停按钮
src/components/LiveCameraPanel.tsx  现场画面面板
src/components/ManualControlPanel.tsx 手动遥控和速度滑块
src/components/LidarPanel.tsx       SVG 雷达扫描可视化
src/components/MapPanel.tsx         地图与机器人位姿展示
tests/                             Node 单元测试
```

`initialRobotStatus` 用于连接前的默认状态；实时数据来自后端。颜色配置保存在浏览器 `localStorage` 并在连接时重新下发，尚未做 Jetson 端跨终端持久化。地图与建图操作已提供代码接口，但完整实车建图验收仍待完成；`navigation` 模式尚未接入 Nav2 导航闭环。

## 验证

从仓库根目录运行：

```bash
node --experimental-strip-types --test web-console/tests/*.test.ts
```

2026-10-17，Windows 本机 Node v24.16.0 验证：**37 passed，0 failed**；`npm ci --no-audit --no-fund` 与 `npm run build` 通过，TypeScript/Vite 6.4.3 生产构建成功。这是软件测试与构建结果，完整硬件、浏览器与长期运行验收另行记录。
