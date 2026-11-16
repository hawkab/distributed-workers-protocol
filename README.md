# Distributed Workers protocol

Portable job, completion, durable effect journal and streaming artifact formats.

- [Wire contract v1](PROTOCOL.md)
- [Connection resource profile](schemas/worker-profile.schema.json)
- [JSON Schemas](schemas/)
- [Runtime choices and primary references](RUNTIMES.md)
- [Compute example](examples/job.json)
- [Portable JS + HTTP example](examples/javascript-http.json)
- [PHP-compatible declarative download](examples/http-artifact.json)
- [HTTPS conformance probe](tools/probe-api.py)

The additive v1 HTTP/artifact extension preserves existing computation jobs and
`run(input,resume)`. The portable JS ABI is `run(input,resume,ctx)`, synchronous
ES5.1, advertised as `javascript.portable.v1`. New `ctx` jobs require that
capability to exclude older executors during rolling updates. PHP without a JS
engine can execute declarative HTTP work. Capability
advertisement describes actual adapter support, not a claim that every task runs
on every operating system. Adapter implementations and real-device acceptance
belong to their component repositories.

## Verification

Run `python3 -B -m unittest discover -s tests -v` (requires `jsonschema`) for
schema/fixture and probe transport checks. The optional `--network-artifacts`
probe exercises the new journal/chunk routes against an explicitly configured
isolated gateway; it does not perform vendor HTTP requests.

Copy `probe.example.json` to ignored `.local/probe.local.json` and configure the
API base and local admin-secret path. `python3 -B tools/probe-api.py --check`
validates settings without any API request. Running without `--check` creates a
unique capability-scoped fixture so it consumes no TV or other workers' jobs.
Do not publish settings, credentials or private endpoint values.
