# v4 地点资源解析契约

当前实现提供只读解析，不创建任务、不发布运动指令，不代表 Nav2 已确认路径可达。

主机入口为 `POST /api/v1/resources/resolve`，需要登录及 `robots.read`、`maps.read`；读取路线还需 `routes.read`。

```json
{
  "robot_id": "当前绑定车辆的业务 ID",
  "references": [
    {"source": "business_place", "resource_id": "地点 ID", "map_id": "地图 ID"}
  ]
}
```

请求禁止额外字段，包括模型生成的坐标。`source` 当前支持 `business_place` 和 `business_route`。路线通过持久化 `parameters.place_ids` 按顺序关联地点；旧路线的自由坐标不能自动推断为地点引用。最多展开 30 个站点，全部必须属于同一张启用地图。

主机读取真实 MapPlace 像素坐标及地图图像版本，将地点交给车端 `POST /api/navigation/resolve-resources`。车端复用现有配准、地图占用和禁行区域检查，返回米制目标、配准内容、控制会话及 `resource_version`。图像版本沿用前端 DrawingDescriptor 的 image_key。

响应中的 `path_validated` 固定为 false。目标点可用并不保证起点到目标存在路径；派发前仍须 Nav2 预览。保存草案后重检时传入 `expected_version`；地点、配准、地图身份、禁行遮罩或安全半径变化会导致版本不匹配并返回 409。

车端已保存地点使用 `source=vehicle_place`，`map_id` 为当前配准图像的 ID。车端从持久化配置读取地点，忽略调用方坐标。车端地点和业务地点分别解析；混合来源尚不能在一次解析中提交。

`GET /api/v1/resources/places?robot_id=...&keyword=...` 返回业务地点和当前地图配准有效时的车端地点，保留同名资源及来源。`GET /api/v1/resources/routes?keyword=...` 返回启用地图上的活动路线。查询结果只表示资源存在，实际可用性必须经过解析核验。

本接口尚未接入助手确认和执行器；直接 SLAM 地点及路线编辑器中的地点选择仍需后续接入。地图文件内容不可变和所有读取路径鉴权也需在整体发布前完成验证。当前测试使用模拟地图与数据库对象，不代表实车验收。
