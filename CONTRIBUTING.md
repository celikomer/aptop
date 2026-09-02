# Contributing

Contributions are welcome. Keep monitoring read-only and preserve the distinction between direct
measurements, runtime-reported values, derived values, and unavailable counters.

Before opening a pull request:

```sh
python -m pip install -e '.[dev]'
pytest
ruff check .
python -m build
```

Do not add device-control operations, credentials, private hostnames, or sample data copied from a
private deployment. Tests should use synthetic fixtures.
