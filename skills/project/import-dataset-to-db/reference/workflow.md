# Import Workflow

Use this workflow for every import request that targets this project.

## Why This Workflow Exists

The previous import attempts were unreliable because the agent:

- called unrelated tools before parsing the file
- wrote ad hoc Python snippets in the shell
- retried multiple inconsistent cleaning strategies
- manually reconstructed rows instead of using one repeatable pipeline
- recursively invoked `bems-agent`, which caused timeouts

This skill avoids those failures by forcing a single preparation script.

## Required Sequence

1. Read the raw file path from the user request.
2. Run the preparation script once:

```bash
uv run python skills/project/import-dataset-to-db/scripts/prepare_import_dataset.py \
  --input "/absolute/path/to/file.csv"
```

3. Inspect the generated run directory under `skills/project/import-dataset-to-db/runtime/`.
4. Read `summary.json`.
5. If `summary.json` reports `status = blocked`, stop and explain why.
6. If `summary.json` reports `demo.records.json`, use that artifact for the `demo` import.
7. If `summary.json` also reports `building_base.records.json`, use that artifact for the supplemental `building_base` import.
8. After import, summarize what succeeded and what was skipped.

## Anti-Patterns

Never do these during this skill:

- call `query_device_logs` or other unrelated MCP query tools just to prepare an import
- launch `uv run bems-agent --message ...` from inside the current agent run
- write `python <<'EOF'` heredoc scripts for import cleaning
- manually rewrite records unless the user explicitly asks for manual repair
- ignore malformed CSV rows without reporting how they were repaired

## Runtime Artifacts

Each run creates a timestamped directory inside `runtime/`:

- `summary.json`: top-level decision report
- `demo.records.json`: cleaned records for `demo`, when ready
- `building_base.records.json`: supplemental building rows, when needed

Treat these artifacts as the canonical cleaned payloads for the import tool call.
