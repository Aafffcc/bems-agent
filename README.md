# BEMS Agent

企业级建筑能源管理 Agent，采用 `CLI 优先 + HTTP API 补充` 的形态，基于 `FastAPI + LangGraph + deepagents + deepagents_cli + PostgreSQL`，使用 `uv` 管理 Python 版本与依赖。

## 技术栈

- Python 3.12
- FastAPI
- LangGraph
- deepagents
- deepagents_cli
- PostgreSQL
- SQLAlchemy
- uv

## 项目结构

```text
.
├── src/bems_agent
│   ├── agent
│   ├── api
│   ├── core
│   ├── db
│   ├── cli.py
│   └── main.py
├── skills/project
├── config
├── tests
├── .env.example
├── .python-version
└── pyproject.toml
```

## 快速开始

1. 安装 Python 3.12

```bash
uv python install 3.12
```

2. 创建虚拟环境并安装依赖

```bash
uv sync --python 3.12
```

3. 配置环境变量

```bash
cp .env.example .env
```

至少补充：

```bash
AGENT_MODEL=<your-provider:model>
```

如果你走 Anthropic 兼容配置，也可以只设置：

```bash
ANTHROPIC_MODEL=claude-3-haiku-20240307
```

4. 启动交互式 CLI

```bash
uv run bems-agent
```

单轮调用示例：

```bash
uv run bems-agent --message "分析 1 号楼昨日能耗"
```

如果只想看最终回答、不显示执行步骤：

```bash
uv run bems-agent --hide-steps
```

5. 启动 HTTP 服务

```bash
uv run bems-agent serve --reload
```

HTTP 服务默认地址（读取 `.env` 中的 `APP_HOST` / `APP_PORT`）：

- API: `http://127.0.0.1:9933`
- Docs: `http://127.0.0.1:9933/docs`

## 当前能力

- 交互式 CLI 主入口
- 默认执行步骤 trace
- 卡片式 MCP/tool 日志输出
- 基于本地 `deepagents` / `deepagents_cli` 组装的 Agent runtime
- 基于 LangGraph checkpointer 的本地 thread 持久化，默认数据库 `~/.bems-agent/sessions.db`
- `AGENTS.md` memory source 自动加载
- skills 加载：
  - 项目技能 `skills/project/`
  - 用户技能 `~/.bems-agent/skills/`
- 项目内置 `import-dataset-to-db` skill，可针对 SQL / JSON / CSV 数据文件执行读取、清洗、字段映射与导入前筛选
- MCP 工具接入
- FastAPI 安全 HTTP API：
  - `POST /api/v1/agent/invoke`
  - `POST /api/v1/agent/stream`
  - `GET /api/v1/health`
- PostgreSQL 异步连接初始化

## HTTP API

当前 HTTP 层只暴露面向前端联调所需的安全接口，不开放 CLI 控制类能力，也不允许通过请求动态切换模型、MCP 或其他运行时配置。

请求约束：

- `user_input` 必填，自动去除首尾空白，且不能是空字符串
- `thread_id` / `session_id` 可选，二者等价；如传入则必须满足安全字符约束
- 请求体默认 `extra="forbid"`，不会接受未声明字段

流式接口说明：

- `POST /api/v1/agent/stream`
- 返回 `text/event-stream`
- 事件类型仅包含：
  - `status`
  - `tool_call`
  - `tool_result`
  - `final_response`
  - `error`
- 不返回模型原始 chain-of-thought，也不会透出完整 tool 原始输出

单轮调用示例：

```bash
curl -X POST http://127.0.0.1:9933/api/v1/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"user_input":"分析 1 号楼昨日能耗","session_id":"thread-123"}'
```

流式调用示例：

```bash
curl -N -X POST http://127.0.0.1:9933/api/v1/agent/stream \
  -H "Content-Type: application/json" \
  -d '{"user_input":"分析 1 号楼昨日能耗","session_id":"thread-123"}'
```

## MCP 配置

默认配置位于 `config/mcp_servers.json`，当前使用远程 `streamable-http`：

```json
{
  "mcpServers": {
    "Energy-precise-data-query": {
      "serverUrl": "http://47.111.9.219:9977/mcp",
      "transport": "streamable-http"
    }
  }
}
```

同时兼容：

- `serverUrl + transport=streamable-http`
- `serverUrl + transport=sse`
- `command + args + transport=stdio`

## Session 与 Skills

默认本地目录：

- `BEMS_HOME=~/.bems-agent`
- Session DB: `~/.bems-agent/sessions.db`
- User skills: `~/.bems-agent/skills`

项目内置 skills 存放在：

- `skills/project/`

如果需要自定义 skills 目录，可以设置：

```bash
BEMS_SKILLS_PATHS=/abs/path/skills-a:/abs/path/skills-b
```

## CLI 命令

交互式 CLI 内支持这些 slash 命令：

- `/help`
- `/skills`
- `/skills list`
- `/mcp`
- `/mcp list`
- `/mcp on`
- `/mcp off`
- `/model`
- `/model <provider:model>`
- `/session`
- `/sessions`
- `/trace`
- `/trace on`
- `/trace off`
- `/exit`

步骤 trace 只展示执行轨迹，例如工具调用和工具结果摘要，不展示模型原始 chain-of-thought。
日志会优先按 `server / tool / args / preview` 结构渲染，而不是把长结果直接原样打印。

其中：

- `/skills list` 会列出当前加载的 skills，并区分 `project`、`global`、`custom`
- `/mcp list` 会列出当前 MCP 配置，并区分 `cloud` 与 `local`，同时显示 transport、endpoint 或 command、cwd、headers 数以及当前已加载的 tool 名称

## 验证命令

```bash
uv run ruff check
uv run pytest
```

## 下一步建议

- 强化 CLI 体验与会话管理
- 接入业务领域模型与数据库表结构
- 将 MCP 查询与本地数据库查询编排到同一工作流
- 增加鉴权、审计与配置分环境管理
