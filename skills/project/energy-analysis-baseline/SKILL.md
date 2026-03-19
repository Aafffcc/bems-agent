---
name: energy-analysis-baseline
description: Use for building energy analysis, anomaly triage, and optimization responses that require disciplined units, time ranges, and evidence-first conclusions.
---

# Energy Analysis Baseline

Use this skill when the user asks about energy consumption, device operating state, alarms, or optimization suggestions.

## Response Rules

- Prefer MCP-grounded facts over assumptions whenever a question depends on live or precise operational data.
- State the time range, building or device scope, and measurement unit when they are known.
- If the available data is incomplete, state what is missing before making recommendations.
- Separate observations, likely causes, and recommended actions instead of mixing them together.

## Output Shape

Default to this structure unless the user asks for another format:

1. Findings
2. Likely causes
3. Recommended next actions

## Guardrails

- Do not fabricate telemetry, alarms, or benchmark values.
- Do not claim savings or fault severity without data support.
- Keep recommendations practical for enterprise operations teams.
