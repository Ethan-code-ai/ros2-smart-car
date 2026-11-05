# v4.0.0 中文模型评估

按用户最新要求，模型验收采用十个典型场景，覆盖状态、停车解释、导航、巡检、运输、静止搜索、路线搜索、报告、顺序任务和高风险指令拒绝。十条均已通过，结果直接取自已完成的 DeepSeek 调用，整理过程增加 API 调用零次。证据位于 `output/v4-deepseek-typical-10/`。

扩展语料 `evaluation/assistant-zh-v1.jsonl` 保留 100 条，用于后续定位边界问题。已完成的扩展轮为 97/100，三条失败记录保留在原输出中。后续调用应显式限定用例，避免重复整轮评估。

评估入口为 `scripts/evaluate_assistant.py`，复用正式的 `assistant_tools.ask`、权限检查、资源解析及数据库报告统计。地点、路线、地图、遥测和事件使用独立内存数据库与模拟车端，数据日期固定在上海时间 2026-11-05。

| 类别 | 数量 | 自动检查 |
|---|---|---|
| 明确任务 | 40 | 步骤数量、动作、地点与路线引用、物品说明、搜索目标 |
| 歧义 | 20 | 没有草案，回复包含澄清或能力限制 |
| 越权与注入 | 20 | 没有草案，非法工具、伪造引用、跨地图、坐标及越权请求受阻 |
| 状态与诊断 | 10 | 调用对应查询工具，回复包含已知位置或停车条件 |
| 事件报告 | 10 | 日期边界、数据库总数、分类和证据引用正确，模型数字不覆盖程序统计 |

扩展轮保留原来的 38/40 明确任务和其他分类全部通过的检查门槛。当前交付使用用户指定的十条典型场景。所有用例均检查没有产生派发或执行记录。回复是否清楚、澄清是否充分、诊断是否超出证据仍需阅读逐条记录。

## 使用自己的模型配置

根目录 `.env` 可填写以下变量。文件受 Git 忽略规则保护，脚本只导入这些模型变量，且不会解释其中的 Shell 命令。

```dotenv
ASSISTANT_BASE_URL=https://api.deepseek.com
ASSISTANT_MODEL=deepseek-flash
ASSISTANT_API_KEY=
```

可用 `ASSISTANT_API_KEY_FILE` 代替直接保存密钥。显式指定环境文件时，以该文件的模型配置为准。

DeepSeek 的官方接口默认开启思考模式。当前适配器对官方 DeepSeek 地址显式设置 `thinking.type=disabled`，使用有时限的普通 Tool Calling 流程。模型名和参数依据 2026-11-05 核对的官方说明。 [接口说明](https://api-docs.deepseek.com/)，[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)

确需重新测量时，在仓库根目录显式指定十条用例，并使用新的输出目录。

```powershell
python scripts/evaluate_assistant.py --mode live --env-file .env --ids D01,D10,T01,T11,T19,T27,T30,R01,T33,S01 --output output/my-deepseek-typical-10
```

少量连接检查可使用 `--ids T01,D01,R01,S01`。少量用例通过时，完整评估门槛仍标为未达到。

## 脚本回放

```powershell
python scripts/evaluate_assistant.py --mode replay --output output/my-assistant-contract-replay
```

回放使用明确编写的工具调用来检查服务端契约，明确任务的输入调用由语料的预期步骤构造。该模式的 `natural_language_gate` 和 `task_accuracy` 为 null。真实提供器评估使用同一用例集和正式提供器适配器。

## 输出与检查范围

每次运行使用新的输出目录。`results.jsonl` 逐条保存回复、草案、报告、工具调用、耗时、模型版本及用量；`summary.json` 保存分类成绩、完整语料标志和各版本摘要。评估器在开始时记录语料、提示词、工具模式、工具实现和自身文件的摘要，便于将结果对应到实际代码。

记录包含固定的模拟地点和事件。原始鉴权头与密钥不进入评估输出。运行不创建执行器，模拟车端只允许查询配置和解析资源。

本轮补齐了草案资源的查询来源检查。只能引用模型已经看到的本轮查询结果，同一批次中猜测 ID 不会获得授权。停车解释也纳入车端避障控制器报告的堵塞、制动、观测不足和传感器故障状态，并保留根因未确认的标记。

真实模型结果和脚本回放结果分别保存在 `output/v4-deepseek-evaluation-01/` 和 `output/v4-model-contract-final/`。发布前还需完成结果人工复核、实际机器人任务验收及当前 Windows 安装包验证。
