---
name: import-dataset-to-db
description: Import user-provided SQL, JSON, or CSV files into the database with a script-first workflow that cleans data, validates `building_id`, and imports primarily into `demo`, only considering `building_base` for new building identities.
---

# Import Dataset To DB

Use this skill when the user asks to import structured data into the database and provides a file such as `sql`, `json`, or `csv`.

This project is bound to two destination tables only:

- `demo`: default target for imported data
- `building_base`: supplemental building master table

Read these files when needed:

- [reference/tables.md](reference/tables.md): exact table schema and field synonym hints
- [reference/workflow.md](reference/workflow.md): end-to-end import procedure and anti-patterns
- [scripts/prepare_import_dataset.py](scripts/prepare_import_dataset.py): required preparation script

## Core Policy

- Default to `demo`.
- Only consider `building_base` when the file introduces a new `building_id`.
- Do not hand-write ad hoc Python for cleaning. Always use the packaged script in this skill directory.
- Do not call unrelated MCP tools such as `query_device_logs` during import preparation.
- Do not recursively call `bems-agent` from inside the import workflow.

## Required Inputs

- At least one referenced or attached data file is required.
- The input file must be parsed by the preparation script before any import tool call.
- If the preparation script reports unresolved schema ambiguity or missing required identity fields, stop and ask the user instead of guessing.

## Mandatory Workflow

1. Read this skill and [reference/workflow.md](reference/workflow.md).
2. Run the preparation script:

```bash
uv run python skills/project/import-dataset-to-db/scripts/prepare_import_dataset.py \
  --input "/absolute/path/to/data.csv"
```

3. Use the script output under `skills/project/import-dataset-to-db/runtime/<run-id>/` as the only source of truth for cleaned records and mapping decisions.
4. Read `summary.json` from that run directory.
5. If `summary.json` says `demo` is ready, import the cleaned `demo.records.json`.
6. If `summary.json` also says a supplemental `building_base.records.json` was generated for new `building_id` values, import that artifact as well.
7. Report:
   - source file
   - chosen target tables
   - cleaned row count
   - row repair issues
   - new `building_id` detection result
   - import result
   - excluded fields or blocked rows

## Preparation Rules

- The preparation script must run before any import attempt.
- Prefer the script artifacts over re-reading the raw file repeatedly.
- If the source is CSV and row length is inconsistent, rely on the script's row repair instead of manually rewriting rows.
- If the source is JSON, let the script flatten only simple one-level nested objects.
- If the source is SQL, let the script parse supported `INSERT INTO ... VALUES ...` statements. If the SQL shape is unsupported, stop and explain the limitation clearly.

## Targeting Rules

- Treat `demo` as the primary landing table.
- Keep imports in `demo` when:
  - the file is monitoring or device data
  - the file contains `monitor_time`, `device_id`, energy metrics, environment metrics, or `cop`
  - the `building_id` already exists in `building_base`
- Consider `building_base` only when:
  - the file introduces a new `building_id`
  - the file contains enough master data to form a reliable building row
  - the building fields are internally consistent

## Guardrails

- Do not fabricate `building_id`, `device_id`, timestamps, or building master data.
- Do not import uncertain field mappings.
- Do not manually patch records line by line unless the user explicitly asks for manual repair.
- Do not bypass the script by writing temporary heredoc scripts in the shell.

## Output Shape

Default to this structure unless the user asks for another format:

1. File and artifact summary
2. Cleaning and repair summary
3. Field mapping summary
4. New `building_id` validation summary
5. Import result
6. Excluded fields or blocked rows
