# BEMS Agent HTTP API

本文档是本项目 HTTP API 的唯一详细文档来源。

以后所有与 API 相关的改动都必须同步更新本文档，包括但不限于：

- 新增或删除接口
- 路由路径变更
- 请求参数、请求头、请求体约束变更
- 响应结构、状态码、错误码变更
- SSE 事件类型与字段变更
- 安全限制、兼容性说明、调用示例变更

## 1. 概览

- 服务框架：FastAPI
- 当前版本：`0.1.0`
- 默认前缀：`/api/v1`
- 默认用途：为项目组前端提供安全、稳定、可复用的 Agent HTTP 调用入口
- 当前鉴权：未接入认证鉴权

当前已暴露接口：

- `GET /api/v1/health`
- `POST /api/v1/agent/invoke`
- `POST /api/v1/agent/stream`

## 2. 设计原则

- HTTP 层只做协议适配，不承载复杂业务逻辑。
- HTTP 层只暴露安全接口，不暴露 CLI 控制能力。
- 不允许通过 HTTP 请求动态切换模型、MCP、skills 或其他运行时开关。
- 不暴露模型原始 chain-of-thought。
- 流式接口中的 tool 相关事件只返回摘要，不返回完整 tool 原始输出。
- `session_id` 仅是对外兼容别名，内部统一按 `thread_id` 处理。

## 3. 通用规则

### 3.1 Base URL

本地默认启动方式：

```bash
uv run bems-agent serve --reload
```

示例地址：

- API：`http://127.0.0.1:9933/api/v1`
- Docs：`http://127.0.0.1:9933/docs`

实际地址以 `.env` 中的 `APP_HOST`、`APP_PORT`、`API_V1_PREFIX` 为准。

### 3.2 Content-Type

- 普通 JSON 接口：`application/json`
- 流式接口：响应为 `text/event-stream`

### 3.3 会话规则

- 如果不传 `thread_id` / `session_id`，服务会创建新会话。
- 如果传入 `thread_id` / `session_id`，服务会尝试续用已有会话。
- 如果指定会话不存在，返回 `404`，错误码为 `session_not_found`。

### 3.4 字段兼容规则

请求中以下字段等价：

- `thread_id`
- `session_id`

服务端统一将其映射为内部 `thread_id`。

### 3.5 请求体安全限制

当前 Agent 请求体采用严格白名单校验：

- 未声明字段会被拒绝
- `user_input` 必填
- `user_input` 会自动去除首尾空白
- 去除空白后 `user_input` 不能为空
- `user_input` 最大长度为 `20000`
- `thread_id` / `session_id` 最大长度为 `128`
- `thread_id` / `session_id` 必须匹配以下正则：

```regex
^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$
```

允许字符说明：

- 首字符：字母或数字
- 后续字符：字母、数字、`.`、`_`、`:`、`-`

不允许示例：

- `../../etc/passwd`
- `/tmp/a`
- ` thread-1`
- `thread 1`

## 4. 错误模型

### 4.1 普通 HTTP 错误

错误响应结构：

```json
{
  "detail": {
    "code": "session_not_found",
    "message": "Session 'missing-thread' was not found."
  }
}
```

当前已定义错误码：

- `session_not_found`
- `mcp_configuration_error`
- `agent_configuration_error`
- `internal_server_error`

### 4.2 422 校验错误

请求体验证失败时，FastAPI 返回标准 `422` 结构，例如：

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "user_input"],
      "msg": "Value error, user_input must not be blank.",
      "input": "   "
    }
  ]
}
```

### 4.3 SSE 错误事件

流式接口内部错误不会切换为新的 HTTP 状态码，而是通过 SSE `error` 事件返回：

```text
event: error
data: {"code":"session_not_found","message":"Session 'missing-thread' was not found."}
```

## 5. 接口明细

---

## 5.1 健康检查

### 请求

- Method：`GET`
- Path：`/api/v1/health`

### 响应

- Status：`200 OK`
- Content-Type：`application/json`

响应体：

```json
{
  "status": "ok",
  "database": "connected"
}
```

字段说明：

- `status`
  - `ok`：数据库连接正常
  - `degraded`：数据库连接异常
- `database`
  - `connected`
  - `disconnected`

### 说明

- 当前健康检查只返回最小健康信息。
- 不返回环境、服务名或其他内部配置细节。

### 示例

```bash
curl http://127.0.0.1:9933/api/v1/health
```

---

## 5.2 单轮调用

### 请求

- Method：`POST`
- Path：`/api/v1/agent/invoke`
- Content-Type：`application/json`

请求体：

```json
{
  "user_input": "分析 1 号楼昨日能耗",
  "session_id": "thread-123"
}
```

字段说明：

- `user_input`
  - 类型：`string`
  - 必填：是
  - 约束：去除首尾空白后不能为空，最大长度 `20000`
- `thread_id`
  - 类型：`string | null`
  - 必填：否
  - 约束：最大长度 `128`，必须匹配安全正则
- `session_id`
  - 类型：`string | null`
  - 必填：否
  - 与 `thread_id` 等价，作为兼容别名使用

注意：

- 请求体不能包含未声明字段。
- 不支持通过该接口传入模型切换参数、MCP 开关、调试开关等运行时控制字段。

### 成功响应

- Status：`200 OK`

响应体：

```json
{
  "thread_id": "thread-123",
  "session_id": "thread-123",
  "created_session": false,
  "response": "昨日 1 号楼整体能耗较前日上升 8.2%，主要增长来自冷机系统。"
}
```

字段说明：

- `thread_id`：内部会话 ID
- `session_id`：对外兼容别名，值等同于 `thread_id`
- `created_session`
  - `true`：本次请求新建会话
  - `false`：本次请求复用已有会话
- `response`：最终回答文本

### 错误响应

- `404 Not Found`

```json
{
  "detail": {
    "code": "session_not_found",
    "message": "Session 'missing-thread' was not found."
  }
}
```

- `502 Bad Gateway`

```json
{
  "detail": {
    "code": "mcp_configuration_error",
    "message": "..."
  }
}
```

- `503 Service Unavailable`

```json
{
  "detail": {
    "code": "agent_configuration_error",
    "message": "..."
  }
}
```

- `422 Unprocessable Entity`

常见触发场景：

- `user_input` 缺失
- `user_input` 为空白字符串
- `thread_id` / `session_id` 不符合安全正则
- 请求体中存在未声明字段

### 示例

新建会话：

```bash
curl -X POST http://127.0.0.1:9933/api/v1/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"user_input":"分析 1 号楼昨日能耗"}'
```

续用已有会话：

```bash
curl -X POST http://127.0.0.1:9933/api/v1/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"user_input":"继续分析冷机异常原因","session_id":"thread-123"}'
```

---

## 5.3 流式调用

### 请求

- Method：`POST`
- Path：`/api/v1/agent/stream`
- Content-Type：`application/json`

请求体与 `POST /api/v1/agent/invoke` 相同。

### 响应

- Status：`200 OK`
- Content-Type：`text/event-stream`

响应头：

- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

### SSE 事件格式

标准格式：

```text
event: <event_name>
data: <json>

```

当前只允许以下事件类型：

- `status`
- `tool_call`
- `tool_result`
- `final_response`
- `error`

### 事件明细

#### 1. `status`

表示阶段状态变化。

示例：

```text
event: status
data: {"title":"Thinking","detail":"model=openai:gpt-5.4,mcp=on"}
```

字段：

- `title`：状态标题
- `detail`：状态说明

常见 `title`：

- `Thinking`
- `Offloading context`
- `Composing response`

#### 2. `tool_call`

表示工具调用开始。

示例：

```text
event: tool_call
data: {"title":"Tool call: list_buildings","detail":"building_id=1","tool_name":"list_buildings","tool_args":{"building_id":"1"}}
```

字段：

- `title`
- `detail`
- `tool_name`
- `tool_args`

说明：

- `detail` 是摘要信息，不保证完整。
- `tool_args` 为已脱敏或安全可展示的参数对象。

#### 3. `tool_result`

表示工具调用结果摘要。

示例：

```text
event: tool_result
data: {"title":"Tool result: list_buildings","detail":"1 building found","tool_name":"list_buildings"}
```

字段：

- `title`
- `detail`
- `tool_name`

说明：

- 不返回完整 tool 原始输出。
- 前端应将其视为展示用摘要，而不是完整业务结果载荷。

#### 4. `final_response`

表示最终回答已完成。

示例：

```text
event: final_response
data: {"thread_id":"thread-123","session_id":"thread-123","response":"分析完成"}
```

字段：

- `thread_id`
- `session_id`
- `response`

说明：

- 该事件是成功流的终结事件。
- 前端应以该事件中的 `response` 作为最终回答。

#### 5. `error`

表示流式执行失败。

示例：

```text
event: error
data: {"code":"session_not_found","message":"Session 'missing-thread' was not found."}
```

字段：

- `code`
- `message`

说明：

- 当出现 `error` 事件时，前端应停止等待后续业务事件。

### 调用示例

```bash
curl -N -X POST http://127.0.0.1:9933/api/v1/agent/stream \
  -H "Content-Type: application/json" \
  -d '{"user_input":"分析 1 号楼昨日能耗","session_id":"thread-123"}'
```

### 前端处理建议

- 按 SSE 标准逐条消费事件。
- 用 `final_response` 作为最终回答结束条件。
- 用 `error` 作为失败结束条件。
- `tool_result` 只适合作为 UI 过程展示，不要直接当作最终业务数据。

## 6. 当前不支持的能力

以下能力当前不通过 HTTP API 暴露：

- 动态切换模型
- 动态开关 MCP
- CLI slash 命令能力
- skills 列表和 sources 浏览
- 会话列表查询
- 本地调试或运行时内部状态查询
- 原始 tool 全量返回
- 模型原始 chain-of-thought 返回

## 7. 兼容性说明

- 当前 API 以项目内 FastAPI 路由为准。
- 当前未实现显式版本协商，仅通过路径前缀 `/api/v1` 表示版本。
- 后续若新增字段，应优先考虑向后兼容。
- 后续若删除字段、修改状态码或更改 SSE 事件结构，必须先同步更新本文档。

## 8. 变更维护要求

以后所有涉及 API 的提交，在合并前必须检查以下内容：

1. `src/bems_agent/api/` 下的路由、模型或行为是否发生变化。
2. 如果有变化，`api.md` 是否已同步更新。
3. `AGENTS.md` 中的架构事实和约束是否仍然正确。
4. 示例请求、示例响应、状态码、错误码、SSE 事件是否仍与实际代码一致。
