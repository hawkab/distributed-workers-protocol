# Distributed Workers protocol

Platform-neutral job, completion and durable receipt formats.

- [Contract](PROTOCOL.md)
- [JSON Schemas](schemas/)
- [Example job](examples/job.json)
- [HTTPS conformance probe](tools/probe-api.py)

Wire protocol version: 1. Executors must advertise capabilities before claiming work.
Android is the currently implemented executor. Platform independence is a contract;
Windows, Linux and PHP execution adapters are not yet shipped.

## Private probe settings

Copy `probe.example.json` to the ignored `.local/probe.local.json` and configure
the API URL and path to the local admin-secret file there. Validate with
`python3 tools/probe-api.py --check`; this issues no API requests. Run the probe
without `--check` only against the intended isolated deployment. Do not publish
the private configuration or credentials.
