---
name: analyze-device-cop
description: Analyze COP (Coefficient of Performance) for a specific device over a requested time range using MCP-grounded data. Use when the user asks in Chinese or English for 设备 COP、制冷/制热 COP、能效系数、performance coefficient、COP 趋势、COP 异常，或某个设备在某个时间段的 COP 结果、解释与排查。
---

# Analyze Device COP

Use this skill to answer requests about one device's COP over a specific period.

## Required Inputs

- Identify one target device. If the request mixes multiple devices, narrow the scope or state that the analysis will be split by device.
- Identify a concrete time range. Resolve relative phrases such as `昨天`, `上周`, or `本月` into explicit start and end times before reasoning.
- Keep the response tied to the user-provided device name or the resolved canonical device identifier.

## Workflow

1. Ask a concise follow-up if the device is missing or ambiguous.
2. Ask for the time range if it is missing. If the range is relative but understandable, convert it into an explicit range.
3. Use `calculate_cop` as the primary MCP tool for COP computation.
4. Use `list_device` only when the device identity needs disambiguation.
5. Use `get_device_status` only when device status helps explain the COP result.
6. Base the conclusion on tool output. If the tool returns no data or errors, state that clearly and avoid unsupported conclusions.

## Analysis Rules

- State the exact time range used in the answer.
- State the device scope explicitly.
- Separate the observed COP result from the interpretation.
- Treat reasons for high or low COP as hypotheses unless the available data proves them.
- Avoid claiming that COP is normal or abnormal unless a benchmark, contract target, or historical baseline is available.
- Do not fabricate thresholds, formulas, telemetry, or benchmark values that the tool output does not provide.

## Output Shape

Default to this structure unless the user asks for another format:

1. COP result
2. Interpretation
3. Likely causes or next checks

## Example Triggers

- `分析 1 号冷机昨天的 COP`
- `帮我看 2026-03-01 到 2026-03-07 这台设备的 COP`
- `这台主机上周 COP 为什么偏低`
- `Explain the COP for chiller-01 during the last 24 hours`
