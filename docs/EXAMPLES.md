# Working examples and live measurements

The examples submit actual jobs to the dedicated queue, wait for canonical
results and check the output. They use no application-specific data or code.
All 49 successful executions were checked; five auxiliary network trials were
explicitly cancelled after source routes stalled. Eleven of the originally
planned 65 trials were not started. None remain pending. The cancelled cases are
not reported as completed execution times.

## Inputs and expected results

| Example | Input | Verified output |
| --- | --- | --- |
| [sha256.json](../examples/sha256.json) | UTF-8 `abc` (3 bytes) | `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad` |
| [word-count.json](../examples/word-count.json) | `alpha beta alpha gamma beta`, repeated 10,000 times | 50,000 tokens; alpha 20,000, beta 20,000, gamma 10,000 |
| [mirror-download.json](../examples/mirror-download.json) | Public version-pinned runtime ZIP, GitFlic mirror | Full 86,195-byte gateway artifact; independent length and SHA-256 check |
| [release-download.json](../examples/release-download.json) | The same signed ZIP, GitHub mirror | The same bytes and hash; source route measured separately |

The expected ZIP SHA-256 is `a781c7c42e75ada0b232c167d4eff77fae3e819e56d68464b69ee920cff686b7`. Its release manifest was
RSA-verified before selecting the fixture. The example is a small functional
streaming check, not a large-file bandwidth test. The worker sends binary bytes
directly to the gateway without a local payload file; the measurement client
independently reads the sealed artifact with a bounded buffer.

The JavaScript example uses the shared synchronous ES5.1 ABI and one checkpoint.
It restarts from that saved state without counting the same rounds again. It was
measured on the actual Android adapter. Node can run it with
`trusted_javascript: true`; PHP does not advertise JavaScript. Node/Linux and PHP
were measured with their implemented built-in hash and HTTP operations.
Windows support is not a Windows performance measurement; no Windows numbers
are inferred from these samples.

## Run against your deployment

```sh
mkdir -p .local
cp examples/benchmark/runner.example.json .local/examples.local.json
# Edit the local API base and owner-readable administrator-token file.
python3 -B tools/run-examples.py \
  --config .local/examples.local.json \
  --state .local/example-run/state.json \
  --output .local/example-run/measurements.json \
  --scenario sha256 --scenario mirror-download --repetitions 5
# On a pool advertising javascript.portable.v1:
python3 -B tools/run-examples.py \
  --config .local/examples.local.json \
  --state .local/word-run/state.json \
  --output .local/word-run/measurements.json \
  --scenario word-count --repetitions 5
```

The client needs an administrator credential to enqueue and read results.
Workers keep their own separate credentials. Pool constraints and allowed worker
IDs go only in the private config. A label does not route a task.

Five repetitions define an experiment; they do not limit task duration or retry
attempts. Ctrl-C preserves the job identities and private state. Repeating the
same command reconciles uncertain enqueue acknowledgements using the original
idempotency keys and resumes observation. The same state refuses a changed plan.
Exporting an already completed state does not enqueue or execute it again.

Private state and canonical receipts contain deployment information; keep them
outside Git. The exported samples omit endpoint URLs, credentials, opaque
routing capabilities, worker IDs, job IDs and filesystem paths. Public example
source URLs are reproducible fixtures, not deployment settings.

## Measurement method

Observed between `2026-11-26T20:55:07.246427+00:00` and `2026-11-26T21:12:33.627831+00:00`
(UTC), spanning 26–27 November in UTC+03. Existing application load stayed
running. Trials were serial within each privately scoped pool; different pools
ran concurrently. Priority 100 selected these small auxiliary jobs ahead of new
ordinary work without cancelling already running application jobs. Existing
resource, concurrency and source-route policies remained in effect.

- **Queue:** gateway job creation to its canonical attempt's start. Earlier
  attempts or requeue time, if present, belong before that start.
- **Execution/result:** canonical attempt start to canonical result-row timestamp;
  this includes task work and result delivery, not pure CPU time.
- **Server total:** job creation to the server's finished timestamp.
- **Client observed total:** submit to reading the canonical result, including
  client transport/observation. It is absent when an interrupted observer resumes.
- **Artifact verification:** an additional readback; its time is recorded
  separately and is not folded into worker execution.

Server timestamps are created inside database transactions. Canonical readback
confirms commit; the samples do not isolate transaction fsync latency. Medians,
minimums and maximums use verified successful completions only. Outcome counts
also include every cancellation and every unstarted trial, avoiding a false
five-of-five claim for an incomplete network cohort. Public-source/proxy routes
strongly affect the download times; the tables are not a ranking of CPU speeds.

## Every observation cohort

| Executor | Task | Completed / planned | Queue, median s | Execution/result, median s | Cancelled | Not started |
| --- | --- | --- | --- | --- | --- | --- |
| Node / Linux | SHA-256: abc | 5 / 5 | 0.032 | 0.795 | 0 | 0 |
| Node / Linux | ZIP stream: GitHub | 2 / 5* | 0.025 | 178.329 | 1 | 2 |
| PHP / shared hosting | SHA-256: abc | 5 / 5 | 1.456 | 0.899 | 0 | 0 |
| PHP / shared hosting | ZIP stream: GitHub | 0 / 5* | — | — | 1 | 4 |
| PHP / RouterOS ARM64 | SHA-256: abc | 5 / 5 | 0.294 | 0.960 | 0 | 0 |
| PHP / RouterOS ARM64 | ZIP stream: GitHub | 3 / 5* | 3.647 | 34.046 | 1 | 1 |
| Android / ARM64 | SHA-256: abc | 5 / 5 | 0.275 | 1.873 | 0 | 0 |
| Android / ARM64 | ZIP stream: GitHub | 5 / 5 | 0.066 | 4.782 | 0 | 0 |
| Android / ARM64 | Word count: 50,000 tokens | 5 / 5 | 0.315 | 2.888 | 0 | 0 |
| Node / Linux | ZIP stream: GitFlic | 5 / 5 | 0.029 | 83.887 | 0 | 0 |
| PHP / shared hosting | ZIP stream: GitFlic | 3 / 5* | 0.362 | 33.910 | 1 | 1 |
| PHP / RouterOS ARM64 | ZIP stream: GitFlic | 1 / 5* | 0.300 | 120.379 | 1 | 3 |
| Android / ARM64 | ZIP stream: GitFlic | 5 / 5 | 0.074 | 6.085 | 0 | 0 |

\* Incomplete download cohort: timing uses only verified completions. Cancelled and unstarted trials remain in the full report. One completed transfer is a functional check, not an estimate of typical throughput.

[All samples, fixture hashes and private-receipt hash references](benchmarks.json)
are stored as plain JSON. This report includes both mirrors, including the
incomplete cohorts. The SHA-256 and word-count cohorts each completed all five
planned trials. Cancelled route experiments retained their canonical ledger and
local evidence; a healthy task is not automatically failed by its age.

## Check the client and examples

```sh
python3 -B -m unittest discover -s tests -v
```

The unit suite validates job schemas, result values, separate timing measures,
full artifact integrity, observer recovery after a lost enqueue reply, replay-free
report regeneration, executor-pool checking and credential-safe redirect refusal.
The fixture tests are separate from the live samples in this report.
