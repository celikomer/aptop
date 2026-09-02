# Runtime state protocol

`aptop` discovers cards and process ownership without help. Runtime-internal details require an
optional JSON state file because the XDMA device nodes do not expose allocator or tensor metadata.

## Location

The default path is `$XDG_RUNTIME_DIR/aptop-workload.json`, falling back to
`/tmp/aptop-$UID/workload.json`. Override it with `APTOP_STATE_FILE` or `--state-file`.

Writers should create a temporary file in the same directory, call `fsync`, and atomically rename
it over the state path. The file should be readable only by its owner.

## Schema

The root object uses `"schema": "aptop-workload/v1"`. All fields except `schema`, `active`, and
`cards` are optional.

- `active`: whether the described workload is currently running.
- `observer_pid` and `child_pid`: optional process IDs used to retire an orphaned active claim.
- `model` / `display_name`: stable model identity; do not include prompt text.
- `phase`: a concise phase such as `loading`, `prefill`, or `decode`.
- `started_at`, `updated_at`, `completed_at`: RFC 3339 timestamps.
- `cards`: one record per participating device.

Each card record identifies `device` and may report:

- `layer_start` and `layer_end_exclusive`.
- `phase`.
- `runtime_memory.capacity_mib`.
- `runtime_memory.weight_mib`.
- `runtime_memory.kv_cache_mib`.
- `runtime_memory.tensor_scratch_mib`.
- `runtime_memory.program_mib`.
- `runtime_memory.unreported_or_reserved_mib`.
- `runtime_memory.known_mapped_mib` and `known_mapped_percent`.

Values are evidence supplied by the runtime integration. `aptop` checks their types and bounds but
does not independently verify them. Completed state can remain available for inspection with the
`L` key; it is hidden from the live view by default.
