# aptop

`aptop` is a read-only, full-screen terminal monitor for Linux hosts with one or two Apex FPGA
accelerator cards. It is designed to feel familiar to users of `btop` and `nvtop` while remaining
honest about the counters the Apex/XDMA software stack actually exposes.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab)
![Linux](https://img.shields.io/badge/platform-Linux-fcc624)
![MIT](https://img.shields.io/badge/license-MIT-45f5a1)

## What it shows

- Host CPU, RAM, load average, and rolling histories.
- Automatic discovery of `xdma0` and `xdma1` from `/dev/xdma*` nodes.
- Which processes currently own each card, using read-only `/proc/<pid>/fd` inspection.
- Card activity history based on process ownership.
- Optional runtime-reported weight, KV-cache, tensor-scratch, program, and reserved mappings.
- Optional model, phase, layer-shard, and elapsed-time metadata.

`aptop` never opens an XDMA device, imports a vendor runtime, starts or stops an inference process,
or modifies the cards. It does not label process ownership as FPGA compute utilization. If the
installed runtime does not expose an occupancy counter, FPGA utilization remains unavailable.

## Install

```sh
git clone <repository-url>
cd aptop
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
aptop
```

For development:

```sh
python -m pip install -e '.[dev]'
pytest
ruff check .
```

## Run

Run the full-screen TUI directly on the Apex host:

```sh
aptop
```

Print one dependency-light snapshot for logs or troubleshooting:

```sh
aptop --once
```

Useful keys:

| Key | Action |
| --- | --- |
| `q` | Quit |
| `p` or `Space` | Pause/resume sampling |
| `r` | Refresh immediately |
| `L` | Show/hide the last completed runtime mapping |
| `?` | Show data-source and measurement help |

By default, card discovery uses `/dev/xdma*`. To monitor an explicit subset:

```sh
aptop --device xdma0
aptop --device xdma0 --device xdma1
```

## Runtime memory and model metadata

Linux can reveal device-node presence and process ownership, but it cannot infer the Apex
runtime's allocator, tensor, KV-cache, program, or weight mappings. An inference program can make
those fields visible by atomically writing the optional `aptop-workload/v1` state file.

```sh
export APTOP_STATE_FILE=/run/user/$UID/aptop-workload.json
aptop
```

See [docs/runtime-state.md](docs/runtime-state.md) and
[examples/workload-state.example.json](examples/workload-state.example.json). Missing fields stay
unavailable; `aptop` never estimates them.

## Permissions

Run `aptop` as the same user that launches the inference workload when possible. Linux security
settings may prevent one user from inspecting another user's `/proc/<pid>/fd` entries. `aptop`
continues running in that case but may not attribute the hidden process to a card. Root is not
required and is not recommended merely for presentation.

## Scope

The first release supports Linux, the XDMA device naming convention, and one or two cards in the
interactive layout. The collector is count-agnostic, but displays with more than two cards have
not yet been optimized.

## License

MIT
