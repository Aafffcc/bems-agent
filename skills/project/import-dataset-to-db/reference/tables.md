# Import Target Tables

Use this reference only when the import target, field mapping, or data cleaning depends on the exact project tables.

## building_base

Use `building_base` for building master data.

Columns:

- `building_id`
- `building_name`
- `building_type`
- `building_area`
- `create_time`

Observed sample shape:

- `building_id`: values like `BUILD-001`, `BUILD-005`
- `building_name`: values like `杭州之门`, `大莲花`, `杭州市中心商场`
- `building_type`: values like `体育场`, `商业综合体`, `甲级写字楼+商业综合体`
- `building_area`: numeric area values like `30000.00`, `229000.00`
- `create_time`: timestamp

High-confidence synonym hints:

- `building_code`, `building_no` -> `building_id`
- `buildingName`, `name`, `project_name` -> `building_name`
- `buildingCategory`, `usage_type` -> `building_type`
- `gross_area`, `area`, `area_m2`, `construction_area` -> `building_area`
- `created_at`, `created_time` -> `create_time`

## demo

Use `demo` for monitoring or device-level time-series data.

Columns:

- `building_id`
- `building_type`
- `monitor_time`
- `electricity_kwh`
- `water_m3`
- `hvac_kwh`
- `supply_water_temp_c`
- `return_water_temp_c`
- `ambient_temp_c`
- `humidity_rh`
- `occupancy_density_per_100m2`
- `device_id`
- `device_status`
- `cop`

Observed sample shape:

- `building_id`: values like `BUILD-005`
- `building_type`: values like `商业综合体`
- `monitor_time`: hourly timestamps like `2017-06-19 00:00:00`
- `device_id`: values like `商场中央空调总电表`
- `device_status`: values like `normal`
- `cop`: numeric values like `2.519`, `3.437`
- Many metrics can be null in the same row

High-confidence synonym hints:

- `building_code`, `building_no` -> `building_id`
- `record_time`, `collect_time`, `ts`, `time` -> `monitor_time`
- `power_kwh`, `electric_kwh` -> `electricity_kwh`
- `water_usage`, `water`, `water_ton` -> `water_m3`
- `aircon_kwh`, `ac_kwh`, `chiller_kwh` -> `hvac_kwh`
- `supply_temp`, `supply_water_temp` -> `supply_water_temp_c`
- `return_temp`, `return_water_temp` -> `return_water_temp_c`
- `outdoor_temp`, `outside_temp` -> `ambient_temp_c`
- `humidity`, `rh` -> `humidity_rh`
- `occupancy_density`, `occupancy_per_100m2` -> `occupancy_density_per_100m2`
- `device_name`, `meter_name`, `equipment_id` -> `device_id`
- `status`, `running_status` -> `device_status`
- `performance_cop` -> `cop`

## Target Selection Heuristics

- Default to `demo` for this project.
- If the file contains timestamps, device identifiers, or energy/environment metrics, prefer `demo`.
- If the file is one row per building and includes area or building name, consider `building_base`.
- If a file contains both building master data and monitoring data, keep `demo` as the primary target and add `building_base` only when a new `building_id` must be created.

## Validation Hints

- `building_base` is the canonical reference for building identity.
- `demo` rows should align with existing `building_id` and, when available, `building_type`.
- Null numeric metrics in `demo` are acceptable if the row still carries valid identity and time context.
- A new `building_id` is the main trigger for evaluating a `building_base` insert.
- Existing `building_id` values should usually stay in `demo` only unless the user explicitly asks to repair building master data.
- The preparation script writes canonical cleaned artifacts into `runtime/<run-id>/`; use those artifacts instead of re-parsing the raw file repeatedly.
