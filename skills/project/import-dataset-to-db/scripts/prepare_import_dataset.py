from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

DEMO_COLUMNS = [
    "building_id",
    "building_type",
    "monitor_time",
    "electricity_kwh",
    "water_m3",
    "hvac_kwh",
    "supply_water_temp_c",
    "return_water_temp_c",
    "ambient_temp_c",
    "humidity_rh",
    "occupancy_density_per_100m2",
    "device_id",
    "device_status",
    "cop",
]

BUILDING_BASE_COLUMNS = [
    "building_id",
    "building_name",
    "building_type",
    "building_area",
    "create_time",
]

DEMO_REQUIRED_FIELDS = ["building_id", "building_type", "monitor_time", "device_id"]
BUILDING_BASE_REQUIRED_FIELDS = ["building_id", "building_name", "building_type"]

NUMERIC_FIELDS = {
    "electricity_kwh",
    "water_m3",
    "hvac_kwh",
    "supply_water_temp_c",
    "return_water_temp_c",
    "ambient_temp_c",
    "humidity_rh",
    "occupancy_density_per_100m2",
    "cop",
    "building_area",
}

DATETIME_FIELDS = {"monitor_time", "create_time"}
NULL_TOKENS = {"", "null", "none", "n/a", "-", "nan"}

DEMO_SYNONYMS = {
    "building_code": "building_id",
    "building_no": "building_id",
    "record_time": "monitor_time",
    "collect_time": "monitor_time",
    "ts": "monitor_time",
    "time": "monitor_time",
    "power_kwh": "electricity_kwh",
    "electric_kwh": "electricity_kwh",
    "water_usage": "water_m3",
    "water": "water_m3",
    "water_ton": "water_m3",
    "aircon_kwh": "hvac_kwh",
    "ac_kwh": "hvac_kwh",
    "chiller_kwh": "hvac_kwh",
    "supply_temp": "supply_water_temp_c",
    "supply_water_temp": "supply_water_temp_c",
    "return_temp": "return_water_temp_c",
    "return_water_temp": "return_water_temp_c",
    "outdoor_temp": "ambient_temp_c",
    "outside_temp": "ambient_temp_c",
    "humidity": "humidity_rh",
    "rh": "humidity_rh",
    "occupancy_density": "occupancy_density_per_100m2",
    "occupancy_per_100m2": "occupancy_density_per_100m2",
    "device_name": "device_id",
    "meter_name": "device_id",
    "equipment_id": "device_id",
    "status": "device_status",
    "running_status": "device_status",
    "performance_cop": "cop",
}

BUILDING_BASE_SYNONYMS = {
    "building_code": "building_id",
    "building_no": "building_id",
    "buildingname": "building_name",
    "name": "building_name",
    "project_name": "building_name",
    "buildingcategory": "building_type",
    "usage_type": "building_type",
    "gross_area": "building_area",
    "area": "building_area",
    "area_m2": "building_area",
    "construction_area": "building_area",
    "created_at": "create_time",
    "created_time": "create_time",
}


@dataclass(slots=True)
class RowIssue:
    row_number: int
    kind: str
    detail: str


@dataclass(slots=True)
class ArtifactSummary:
    status: str
    input_file: str
    detected_format: str
    target_table: str
    raw_row_count: int
    cleaned_row_count: int
    row_issues: list[RowIssue] = field(default_factory=list)
    field_mapping: dict[str, str] = field(default_factory=dict)
    excluded_source_fields: list[str] = field(default_factory=list)
    missing_required_fields: list[str] = field(default_factory=list)
    existing_building_ids: list[str] = field(default_factory=list)
    new_building_ids: list[str] = field(default_factory=list)
    building_base_action: str = "not_needed"
    artifacts: dict[str, str] = field(default_factory=dict)


def normalize_key(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-zA-Z0-9_]", "", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_").lower()


def discover_project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def load_postgres_dsn(project_root: Path) -> str | None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return None
    env_dsn = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "POSTGRES_DSN":
            env_dsn = value.strip()
            break
    return env_dsn


def flatten_one_level(record: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                combined = f"{key}_{child_key}"
                flattened[combined] = child_value
        else:
            flattened[key] = value
    return flattened


def is_nullish(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in NULL_TOKENS
    return False


def parse_datetime_text(value: str) -> str:
    patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]
    for pattern in patterns:
        try:
            parsed = datetime.strptime(value, pattern)
            if pattern == "%Y-%m-%d":
                return parsed.strftime("%Y-%m-%d 00:00:00")
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"Unsupported datetime format: {value}")


def coerce_value(field_name: str, value: Any) -> Any:
    if is_nullish(value):
        return None
    if isinstance(value, str):
        value = value.strip()
    if field_name in NUMERIC_FIELDS:
        return float(value)
    if field_name in DATETIME_FIELDS and isinstance(value, str):
        return parse_datetime_text(value)
    return value


def deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def map_fields(source_fields: list[str], target_table: str) -> tuple[dict[str, str], list[str]]:
    schema = DEMO_COLUMNS if target_table == "demo" else BUILDING_BASE_COLUMNS
    synonyms = DEMO_SYNONYMS if target_table == "demo" else BUILDING_BASE_SYNONYMS

    normalized_schema = {normalize_key(field): field for field in schema}
    field_mapping: dict[str, str] = {}
    excluded: list[str] = []

    for source_field in source_fields:
        normalized = normalize_key(source_field)
        if normalized in normalized_schema:
            field_mapping[source_field] = normalized_schema[normalized]
            continue
        if normalized in synonyms:
            field_mapping[source_field] = synonyms[normalized]
            continue
        excluded.append(source_field)

    return field_mapping, excluded


def infer_target_table(source_fields: list[str]) -> str:
    normalized = {normalize_key(field) for field in source_fields}
    demo_signals = {
        "monitor_time",
        "device_id",
        "electricity_kwh",
        "water_m3",
        "hvac_kwh",
        "cop",
        "supply_water_temp_c",
        "return_water_temp_c",
        "ambient_temp_c",
        "humidity_rh",
    }
    building_signals = {"building_name", "building_area", "create_time"}
    if normalized & demo_signals:
        return "demo"
    if normalized & building_signals:
        return "building_base"
    return "demo"


def reconcile_demo_csv_row(row: list[str]) -> list[str]:
    if len(row) <= len(DEMO_COLUMNS):
        return row + [""] * (len(DEMO_COLUMNS) - len(row))

    prefix = row[:3]
    suffix = row[-3:]
    middle = row[3:-3]
    while len(middle) > 8 and middle and middle[-1] == "":
        middle.pop()
    if len(middle) > 8:
        middle = middle[:8]
    if len(middle) < 8:
        middle.extend([""] * (8 - len(middle)))
    return prefix + middle + suffix


def load_csv_records(input_path: Path) -> tuple[list[dict[str, Any]], list[str], list[RowIssue]]:
    issues: list[RowIssue] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    if not rows:
        raise ValueError("CSV file is empty.")

    header = rows[0]
    target_guess = infer_target_table(header)
    records: list[dict[str, Any]] = []

    for row_number, raw_row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in raw_row):
            continue
        fixed_row = raw_row
        if len(raw_row) != len(header):
            if target_guess == "demo":
                fixed_row = reconcile_demo_csv_row(raw_row)
                issues.append(
                    RowIssue(
                        row_number=row_number,
                        kind="row_length_repaired",
                        detail=f"expected {len(header)} columns, got {len(raw_row)}",
                    )
                )
            else:
                if len(raw_row) < len(header):
                    fixed_row = raw_row + [""] * (len(header) - len(raw_row))
                else:
                    fixed_row = raw_row[: len(header)]
                issues.append(
                    RowIssue(
                        row_number=row_number,
                        kind="row_length_adjusted",
                        detail=f"expected {len(header)} columns, got {len(raw_row)}",
                    )
                )
        record = dict(zip(header, fixed_row, strict=False))
        records.append(record)
    return records, header, issues


def load_json_records(input_path: Path) -> tuple[list[dict[str, Any]], list[str], list[RowIssue]]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    issues: list[RowIssue] = []
    if isinstance(payload, dict):
        raw_records = [payload]
    elif isinstance(payload, list):
        raw_records = payload
    else:
        raise ValueError("JSON payload must be an object or an array of objects.")

    records: list[dict[str, Any]] = []
    source_fields: list[str] = []
    for index, item in enumerate(raw_records, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"JSON item {index} is not an object.")
        flattened = flatten_one_level(item)
        records.append(flattened)
        for key in flattened:
            if key not in source_fields:
                source_fields.append(key)
    return records, source_fields, issues


def split_sql_tuples(values_block: str) -> list[str]:
    tuples: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote = False
    previous = ""
    for char in values_block:
        current.append(char)
        if char == "'" and previous != "\\":
            in_quote = not in_quote
        elif char == "(" and not in_quote:
            depth += 1
        elif char == ")" and not in_quote:
            depth -= 1
            if depth == 0:
                tuples.append("".join(current).strip().strip(","))
                current = []
        previous = char
    return [item for item in tuples if item]


def parse_sql_tuple(tuple_text: str) -> list[Any]:
    payload = tuple_text.strip()
    if payload.startswith("(") and payload.endswith(")"):
        payload = payload[1:-1]
    reader = csv.reader([payload], delimiter=",", quotechar="'", skipinitialspace=True)
    row = next(reader)
    values: list[Any] = []
    for value in row:
        stripped = value.strip()
        if stripped.upper() == "NULL":
            values.append(None)
        else:
            values.append(stripped)
    return values


def load_sql_records(input_path: Path) -> tuple[list[dict[str, Any]], list[str], list[RowIssue]]:
    text_content = input_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"INSERT\s+INTO\s+[^\s(]+\s*\((?P<columns>.*?)\)\s*VALUES\s*(?P<values>.*?);",
        flags=re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(text_content))
    if not matches:
        raise ValueError("Only SQL files with INSERT INTO ... VALUES ... statements are supported.")

    all_records: list[dict[str, Any]] = []
    source_fields: list[str] = []
    for match in matches:
        columns = [column.strip().strip('"') for column in match.group("columns").split(",")]
        value_tuples = split_sql_tuples(match.group("values"))
        for tuple_text in value_tuples:
            values = parse_sql_tuple(tuple_text)
            if len(values) != len(columns):
                raise ValueError("SQL INSERT tuple length does not match the column list.")
            record = dict(zip(columns, values, strict=False))
            all_records.append(record)
        for column in columns:
            if column not in source_fields:
                source_fields.append(column)
    return all_records, source_fields, []


def load_records(input_path: Path) -> tuple[list[dict[str, Any]], list[str], list[RowIssue], str]:
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        records, fields, issues = load_csv_records(input_path)
        return records, fields, issues, "csv"
    if suffix == ".json":
        records, fields, issues = load_json_records(input_path)
        return records, fields, issues, "json"
    if suffix == ".sql":
        records, fields, issues = load_sql_records(input_path)
        return records, fields, issues, "sql"
    raise ValueError(f"Unsupported file format: {suffix}")


def clean_records(
    source_records: list[dict[str, Any]],
    field_mapping: dict[str, str],
    target_table: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    required_fields = (
        DEMO_REQUIRED_FIELDS
        if target_table == "demo"
        else BUILDING_BASE_REQUIRED_FIELDS
    )
    cleaned: list[dict[str, Any]] = []

    for record in source_records:
        transformed: dict[str, Any] = {}
        for source_field, target_field in field_mapping.items():
            if source_field not in record:
                continue
            transformed[target_field] = coerce_value(target_field, record[source_field])
        if any(transformed.get(field) in {None, ""} for field in required_fields):
            continue
        cleaned.append(transformed)

    deduplicated = deduplicate_records(cleaned)
    missing_required = [
        field
        for field in required_fields
        if not any(record.get(field) for record in deduplicated)
    ]
    return deduplicated, missing_required


def fetch_existing_building_ids(postgres_dsn: str | None) -> set[str]:
    if not postgres_dsn:
        return set()
    engine = create_engine(postgres_dsn)
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT building_id FROM public.building_base"))
            return {str(row[0]) for row in result if row[0]}
    finally:
        engine.dispose()


def build_building_base_candidates(source_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_fields = list({key for record in source_records for key in record})
    field_mapping, _ = map_fields(source_fields, "building_base")
    grouped: dict[str, dict[str, Any]] = {}
    for record in source_records:
        candidate: dict[str, Any] = {}
        for source_field, target_field in field_mapping.items():
            if source_field not in record:
                continue
            candidate[target_field] = coerce_value(target_field, record[source_field])
        building_id = candidate.get("building_id")
        if not building_id:
            continue
        current = grouped.setdefault(str(building_id), {})
        for key, value in candidate.items():
            if value is not None and key not in current:
                current[key] = value

    results: list[dict[str, Any]] = []
    for candidate in grouped.values():
        if all(candidate.get(field) for field in BUILDING_BASE_REQUIRED_FIELDS):
            results.append(candidate)
    return deduplicate_records(results)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_preparation(input_path: Path, output_dir: Path) -> ArtifactSummary:
    project_root = discover_project_root()
    postgres_dsn = load_postgres_dsn(project_root)

    source_records, source_fields, row_issues, detected_format = load_records(input_path)
    target_table = infer_target_table(source_fields)
    field_mapping, excluded_source_fields = map_fields(source_fields, target_table)
    cleaned_records, missing_required_fields = clean_records(
        source_records,
        field_mapping,
        target_table,
    )

    existing_building_ids = sorted(fetch_existing_building_ids(postgres_dsn))
    existing_building_id_set = set(existing_building_ids)

    target_records_path = output_dir / f"{target_table}.records.json"
    artifacts = {"summary": str(output_dir / "summary.json")}

    new_building_ids: list[str] = []
    building_base_action = "not_needed"

    if target_table == "demo":
        demo_building_ids = sorted(
            {
                str(record["building_id"])
                for record in cleaned_records
                if record.get("building_id") is not None
            }
        )
        new_building_ids = sorted(
            [item for item in demo_building_ids if item not in existing_building_id_set]
        )

    status = "ready"
    if missing_required_fields or not cleaned_records or not field_mapping:
        status = "blocked"

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(target_records_path, cleaned_records)
    artifacts[target_table] = str(target_records_path)

    if target_table == "demo" and new_building_ids:
        candidates = [
            candidate
            for candidate in build_building_base_candidates(source_records)
            if str(candidate.get("building_id")) in new_building_ids
        ]
        if candidates:
            building_base_path = output_dir / "building_base.records.json"
            write_json(building_base_path, candidates)
            artifacts["building_base"] = str(building_base_path)
            building_base_action = "supplemental_import_ready"
        else:
            building_base_action = "new_building_id_detected_but_metadata_incomplete"

    summary = ArtifactSummary(
        status=status,
        input_file=str(input_path),
        detected_format=detected_format,
        target_table=target_table,
        raw_row_count=len(source_records),
        cleaned_row_count=len(cleaned_records),
        row_issues=row_issues,
        field_mapping=field_mapping,
        excluded_source_fields=excluded_source_fields,
        missing_required_fields=missing_required_fields,
        existing_building_ids=existing_building_ids,
        new_building_ids=new_building_ids,
        building_base_action=building_base_action,
        artifacts=artifacts,
    )
    write_json(output_dir / "summary.json", asdict(summary))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and validate an import dataset.")
    parser.add_argument("--input", required=True, help="Absolute path to the source data file.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Optional output directory. Defaults to a timestamped folder "
            "inside the skill runtime directory."
        ),
    )
    return parser


def default_output_dir() -> Path:
    runtime_root = Path(__file__).resolve().parents[1] / "runtime"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return runtime_root / timestamp


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        raise SystemExit(f"Input file not found: {input_path}")

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else default_output_dir()
    )
    summary = run_preparation(input_path, output_dir)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0 if summary.status == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
