# BEMS Agent Development Guide

本文件用于为 AI CLI 工具提供本项目的开发上下文、约束和执行规范。

## Project Overview

- 项目名称：`bems-agent`
- 项目目标：开发企业级建筑能源管理 Agent，提供 CLI 优先的交互入口，并保留 HTTP API
- 项目类型：CLI-first Agent 应用
- 当前阶段：完成 CLI-first 骨架、deepagents/deepagents_cli 能力复用、thread/checkpointer 会话持久化、MCP 兼容层，以及基于 `prompt_toolkit` 的 slash 命令自动匹配与二级动作面板
- 技术栈：
  - Python 3.12
  - `uv`
  - FastAPI
  - LangGraph
  - deepagents
  - PostgreSQL
  - MCP tools

## Current Architecture

- 主入口：`bems-agent` 默认进入交互式 CLI
- HTTP 服务：`bems-agent serve`
- Agent runtime：基于本地 `deepagents` + `deepagents_cli` 中间件与工具显示能力组装
- 工作流运行时：LangGraph
- 会话存储：LangGraph SQLite checkpointer，默认数据库 `~/.bems-agent/sessions.db`
- CLI trace：默认展示执行步骤，不暴露原始 chain-of-thought，只显示工具调用与结果摘要
- CLI trace 渲染：复用 `deepagents_cli.tool_display` 的紧凑展示策略，避免把长 JSON / 长文本原样倾倒到终端
- CLI slash 交互：交互模式下使用 `prompt_toolkit` 提供 slash 命令自动匹配、当前项高亮、上下键切换、右键/Enter 选中、左键/Esc 关闭，以及 `/skills`、`/mcp`、`/model`、`/trace` 的二级 action 面板
- CLI 文件引用交互：支持通过 `@filename` 自动补全项目内文件，并在提交消息时把引用文件内容以内联上下文形式注入给 agent；超大文件只注入路径与大小提示，不直接嵌入全文
- CLI 退出清理：CLI 进程结束前必须显式执行 runtime shutdown，先释放 MCP session 再结束事件循环，避免正常 `/exit` 时出现异步生成器清理错误日志
- 上下文加载：通过 `MemoryMiddleware` 加载 `AGENTS.md`，默认来源包括项目根 `AGENTS.md`、`.deepagents/AGENTS.md` 和 `${BEMS_HOME}/AGENTS.md`
- Skills：
  - 项目技能目录：`skills/project/`
  - 用户技能目录：`~/.bems-agent/skills/`
- 数据库：PostgreSQL，仅作为服务依赖的一部分，当前未承载业务表
- MCP：
  - 默认配置文件：`config/mcp_servers.json`
  - 当前默认 server：`Energy-precise-data-query`
  - 地址：`http://47.111.9.219:9977/mcp`
  - 传输方式：`streamable-http`
  - 兼容格式：`streamable-http`、`sse`、`stdio`

## Repository Structure

```text
.
├── skills/
│   └── project/
├── config/
│   └── mcp_servers.json
├── src/bems_agent/
│   ├── agent/
│   │   ├── exceptions.py
│   │   ├── graph.py
│   │   ├── mcp.py
│   │   ├── prompts.py
│   │   ├── sessions.py
│   │   └── service.py
│   ├── api/
│   ├── core/
│   ├── db/
│   ├── cli.py
│   └── main.py
├── tests/
├── pyproject.toml
└── README.md
```

## Key Local Dependencies

- 本项目通过 `uv` 管理依赖。
- 本项目依赖本机已存在的 `deepagents` 源码，不使用远程 PyPI 版本。
- 本地 deepagents 路径：
  - `/Library/WorkSpace Python/deepagents/libs/deepagents`
- 本地 deepagents-cli 路径：
  - `/Library/WorkSpace Python/deepagents/libs/cli`

## Environment Variables

最关键的环境变量如下：

```env
APP_NAME=BEMS Agent
APP_ENV=local
APP_HOST=0.0.0.0
APP_PORT=8000
APP_DEBUG=true
API_V1_PREFIX=/api/v1
POSTGRES_DSN=postgresql+psycopg://postgres:postgres@localhost:5432/bems_agent
AGENT_MODEL=<provider:model>
ANTHROPIC_MODEL=<anthropic-model-name>
MCP_ENABLED=true
MCP_CONFIG_PATH=config/mcp_servers.json
BEMS_HOME=~/.bems-agent
BEMS_SESSION_DIR=
BEMS_SKILLS_PATHS=
```

说明：

- 优先使用 `AGENT_MODEL`。
- 如果未设置 `AGENT_MODEL`，会回退到 `ANTHROPIC_MODEL`。
- 推荐使用支持 tool calling 的模型。
- `MCP_CONFIG_PATH` 当前默认指向项目内置配置。
- `BEMS_SESSION_DIR` 未显式设置时，默认落到 `${BEMS_HOME}/sessions`。
- Checkpointer 数据库默认落到 `${BEMS_HOME}/sessions.db`。
- `BEMS_SKILLS_PATHS` 未显式设置时，默认加载：
  - `skills/project`
  - `${BEMS_HOME}/skills`

## Development Rules

### General Rules

- 优先复用现有 `deepagents` 能力，不要重复造一套 agent runtime。
- 默认先考虑 CLI 场景，再考虑 HTTP API 的协议适配。
- 新增能力时，优先在 `src/bems_agent/agent/` 下扩展共享逻辑。
- API 层只负责协议适配，不承载复杂业务逻辑。
- MCP 配置兼容用户自定义格式，尤其是：
  - `serverUrl`
  - `transport = streamable-http`
- 所有新增 Python 代码必须带类型标注。
- 每完成一个重要节点，必须同步更新本文件，让后续开发以最新事实为准。
- 修改后必须至少运行：
  - `uv run pytest`
  - `uv run ruff check`
- 每次执行code的更新或者增减功能的时候，先进行自主的code review，确认无误后正式修改。
- 不需要每次执行都写一份test.py来验证功能，只需要验证核心的逻辑和功能。

### Code Organization

- `api/`：HTTP 路由、请求响应模型、接口错误处理
- `agent/`：Agent runtime、会话服务、prompt、MCP 接入、工作流编排
- `db/`：数据库连接、会话、后续 ORM 模型
- `core/`：配置、基础设施级公共逻辑
- `skills/project/`：项目内置 skills

### Database Rules

- 数据库是远程 PostgreSQL。
- 不要把数据库账号密码硬编码进源码。
- 真实数据库 DSN 只允许放在本地 `.env` 或等价的私有环境注入中，不写入版本管理文件。
- 新增数据库模型时，保持与建筑能源业务语义一致。
- 后续如引入 Alembic，迁移文件统一纳入版本管理。

### MCP Rules

- 优先通过 MCP 获取精确能源数据，而不是在模型里猜测。
- 如果 MCP 不可用，接口应返回明确错误，不要静默降级成伪结果。
- 如果新增 MCP server，统一维护在 `config/mcp_servers.json`。
- 默认配置优先使用远程 `streamable-http` 方式；如切换为 `stdio`，必须同步更新文档。
- 当前 `Energy-precise-data-query` 的工具对外统一使用这些规范名：
  - `query_device_logs`
  - `calculate_cop`
  - `get_device_status`
  - `list_buildings`
  - `list_device`
  - `import_dataset`

### Session Rules

- v1 会话模型是本地单用户持久化，不做多租户与数据库会话表。
- CLI 与 HTTP API 必须复用同一套 session service。
- 会话恢复以 LangGraph `thread_id + checkpointer` 为准，不要在 API 层重复实现消息历史拼装。
- `session_id` 目前仅作为对外兼容别名，内部统一按 `thread_id` 处理。
- CLI 内允许会话级切换：
  - `/model <provider:model>`
  - `/mcp on|off`
  - `/trace on|off`

### CLI Rules

- 默认交互式 CLI 必须提供 slash 命令入口，至少包括：
  - `/help`
  - `/skills`
  - `/skills list`
  - `/skills sources`
  - `/mcp`
  - `/mcp list`
  - `/model`
  - `/session`
  - `/sessions`
  - `/trace`
  - `/exit`
- slash 命令输入交互应尽量采用命令面板式体验：
  - 输入 `/` 后自动匹配命令
  - 当前选中项高亮
  - 支持上下键切换、右键/Enter 选中、左键/Esc 返回
  - 在命令右侧展示简短 tip / description
- CLI 输入应支持 `@filename` 文件引用：
  - 输入 `@` 后自动补全项目内文件
  - 上下键切换候选，右键/Enter 选中候选
  - 提交消息时自动把引用文件内容附加到 prompt 上下文
  - 对超大文件只附加路径与大小提示，避免直接灌入长文本
- 执行步骤输出只显示高价值轨迹：
  - tool call
  - tool result
  - start / finish 状态
- MCP / tool 日志应优先展示：
  - server
  - tool
  - args
  - preview
- 不要输出模型原始思维链或伪造的“思考内容”。
- `/skills list` 面向终端用户时默认只展示：
  - skill name
  - skill description
- skill source path / file path 不应出现在 `/skills list` 主视图中；如需查看目录来源，使用 `/skills sources`
- `/mcp list` 必须明确区分：
  - cloud/http(sse) MCP
  - local/stdio MCP
  - 并展示 transport、endpoint 或 command、cwd、headers 数、当前已加载 tool 名称

### Test Rules

- 只保留关键路径测试：配置解析、MCP 规范化、会话存储、CLI 主流程、核心 API。
- 删除模板味重、收益低、和当前产品形态不匹配的测试。
- 新测试必须优先验证共享 service 层行为，而不是只测薄包装。

## Expected Agent Responsibilities

这个 Agent 后续主要承担：

- 建筑能耗分析
- 设备运行状态分析
- 告警与异常定位辅助
- 运行优化建议生成
- 面向企业业务场景的能源数据问答

## Recommended Next Development Order

建议后续按下面顺序继续开发：

1. 强化 CLI 体验：
   - 会话列表/恢复
   - 更清晰的启动 banner
   - 单轮与交互模式的输出规范
   - slash 命令继续补齐，例如 `/new`、`/clear`
2. 定义建筑能源领域的核心 prompt、skills 和行为边界
3. 接入真实 PostgreSQL 表结构与数据访问层
4. 设计能耗分析、设备诊断、告警处理等工具层
5. 将 MCP 查询与本地数据库查询编排到同一工作流
6. 增加鉴权、日志、审计、配置分环境管理
7. 引入数据库迁移和更贴近业务的集成测试

## Common Commands

安装依赖：

```bash
uv sync --python 3.12
```

启动交互式 CLI：

```bash
uv run bems-agent
```

单轮调用：

```bash
uv run bems-agent --message "分析 1 号楼昨日能耗"
```

关闭步骤 trace：

```bash
uv run bems-agent --hide-steps
```

启动 HTTP 服务：

```bash
uv run bems-agent serve --reload
```

运行测试：

```bash
uv run pytest
```

运行静态检查：

```bash
uv run ruff check
```

## AI CLI Working Style

AI CLI 在本项目中应遵循以下方式：

- 先读现有代码，再改代码
- 尽量做增量修改，不要大面积推翻已有结构
- 优先保持当前 `deepagents` 集成方式稳定
- 默认保持 CLI-first 架构，不要把核心逻辑重新推回 FastAPI 路由层
- 对外部依赖版本变更保持谨慎，尤其是：
  - `deepagents`
  - `langchain`
  - `langgraph`
  - `langchain-mcp-adapters`
- 如果引入新基础设施，先保证最小可运行版本
- 完成修改后必须做本地验证

## Notes

- 当前项目已完成：
  - CLI-first 入口
  - deepagents / deepagents_cli 集成
  - skills 目录接入
  - 基于 SQLite checkpointer 的 thread 持久化
  - `AGENTS.md` memory source 接入
  - 执行步骤 trace
  - slash 命令控制层
  - 基于 `prompt_toolkit` 的 slash 命令自动匹配与 action 面板
  - `@filename` 项目文件自动补全与引用文件内容注入
  - `/skills list` 的 name/description 简洁视图与 `/skills sources` 目录视图
  - `/mcp list` 元数据视图
  - MCP 接入与规范化
  - CLI 正常退出时的 runtime / MCP 显式清理与幂等容错关闭
  - 基础 HTTP API
- 当前项目尚未接入真实业务模型、数据库表和业务工具链。
- 当前 `agent/invoke` 已具备 session 透传语义，可作为服务化扩展入口。
