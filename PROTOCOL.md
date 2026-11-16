# Worker protocol v1

This is an additive version 1 contract. Existing `sha256`, `text.normalize`,
`javascript`, claim, heartbeat and completion messages remain valid. Executors
advertise only implemented capabilities. A portable contract does not imply that
every runtime, native application or GPU workload runs on every host.

## Connection, resources and private worker views

A worker receives a separate enrollment credential through private provisioning.
An administrator creates it with `POST /v1/admin/enrollments {}` (201,
`{enrollment_token}`). No administrator key is installed on a worker. This
credential is unique per installation; sharing it shares the same participant.

Before consuming work, send `POST /v1/workers/connect {}` with its Bearer
credential and `X-Worker-Profile`. The header is unpadded base64url of compact
UTF-8 JSON, following [the profile schema](schemas/worker-profile.schema.json):

```json
{"schema":1,"capabilities":["sha256","http.request","artifact.stream"],
 "resources":{"cpu_logical_cores":8,"cpu_available_cores":2.5,
 "ram_total_bytes":8589934592,"ram_available_bytes":4294967296,
 "disk_total_bytes":107374182400,"disk_available_bytes":64424509440,
 "gpu_status":"unknown","gpus":[]}}
```

The server atomically assigns an opaque UUID and returns
`{worker_id,update_poll_seconds,profile_schema:1}`. The same credential on a retry
or restart returns the same ID, including after a lost response. Keep using that
Bearer credential; an ID is an identifier, never authorization. Persist the ID
before delivering an outbox or claiming work. Existing enrolled device tokens
keep their old IDs. An unexpected identity change with pending durable work
must be reported without discarding that work.

Refresh the header on worker heartbeats and claims. Capability arrays in the
header and an optional legacy body must agree. The server records its own
observation time; client clocks cannot make resource reports fresh. A resource
constrained claim uses the profile from that exact RPC, not an old heartbeat.
Legacy clients without a header can only claim jobs without hard requirements.

Resource counts are actual available budgets: CPU affinity/quota, process and
cgroup memory limits, and free space on the worker state filesystem apply.
Unknown values are null/omitted; an unqueried GPU is not zero GPUs. Each detected
GPU has an index, optional compute-unit count, total/available memory and a
nullable `unified_memory` flag. A driver-reported compute unit is not inferred
from a model name. Profiles contain no hostnames, serial numbers, credentials or
hardware model strings.

A job may include [resource_requirements](schemas/resource-requirements.schema.json):
`{cpu_cores,ram_bytes,disk_bytes,gpu_count,gpu_compute_units,gpu_memory_bytes}`.
Only positive requirements restrict admission. GPU compute units and memory are
per device; either implies at least one GPU. Unknown resources cannot prove a
positive requirement. Unified GPU memory must fit its reported allocation budget
and the shared RAM budget together with CPU RAM; it must not be counted twice.
No unrelated minimum or fixed resource safety multiplier is imposed.

A device has two read views:

- `GET /v1/workers/me`: its ID, profile, `server_observed_at` and update policy.
- `GET /v1/jobs/current`: 200 `{job:...}` for its current live fenced assignment,
  or 204. This read does not renew a lease or allocate another job.

There is no device endpoint listing the shared backlog, other workers, completed
history or another participant's messages. Device job/effect/artifact reads are
checked against its current live assignment. Once that assignment expires or
finishes, old local IDs and attempt tokens do not authorize reading it. A new
assignee may recover that same job's artifacts, because the assignment is now
its own. Global administration remains separately authenticated. Late own result,
artifact and effect **writes** can still receive durable candidate receipts;
this does not grant access to another attempt's response or messages.

## Jobs, leases and completion

The configured HTTPS API base may contain a path prefix. Paths below are relative
to that base. Device requests carry `Authorization: Bearer <device token>`; admin
credentials are separate and never distributed to workers or task scripts.

| Request | Body / response |
| --- | --- |
| `POST /v1/workers/heartbeat` | `{worker_id,version_code,version_name,capabilities:[],abis:[],state,active_job_id?}` → 200 |
| `POST /v1/jobs/claim` | `{worker_id,capabilities:[]}` → 204 or `{job:{id,operation,input,script?,network?,attempt_id,fencing_token,lease_until}}` |
| `POST /v1/jobs/{job}/heartbeat` | `{worker_id,attempt_id,fencing_token,progress:{}}` → renewable `lease_until`, optional `cancel_requested` |
| `POST /v1/jobs/{job}/complete` | `{worker_id,attempt_id,fencing_token,status:succeeded\|failed,result,error?:{code,message},artifact_ids?:[]}` |

Completion returns 200 `{job_id,attempt_id,committed:true}`. An identical retry
gets the same durable acknowledgement. A stale completion can be retained as
`{job_id,attempt_id,committed:false,candidate_saved:true}`. A device removes an
outbox entry only after a matching durable receipt. Completion artifact IDs must
be sealed references belonging to the same job. A current fenced attempt may
reuse that job's earlier attempt artifacts; another job cannot adopt them.

A lease detects a lost worker; it is not a job deadline. Heartbeats continue during
computation, source reads and upload backpressure. Explicit cancellation stops
new source operations and closes active streams while retaining acknowledged
bytes/checkpoints. An offline device resumes from its durable state/outbox;
it must reconcile its lease before committing. No fixed attempt exhaustion or
default overall runtime/response-size deadline is part of this protocol. Actual
memory, storage and hosting request limits require admission or checkpointed
yield, with a concrete error rather than silent data loss.

Execution is at least once. Fencing and idempotent persistence protect canonical
results; they do not promise exactly-once external HTTP side effects.

## Portable instructions

`sha256` takes `{text}` and returns `{sha256}`. `text.normalize` takes `{text}` and
returns `{text}` after NFC normalization, Unicode whitespace collapse and trim.

`javascript` carries source defining synchronous ES5.1:

```javascript
function run(input, resume, ctx) {
  var start = resume ? resume.next : 0;
  var sum = resume ? resume.sum : 0;
  for (var i = start; i < input.count; i++) {
    sum += i;
    if (i % 1000 === 0) {
      ctx.checkpoint({next: i + 1, sum: sum});
      ctx.progress({processed: i + 1});
    }
  }
  return {sum: sum};
}
```

The second argument remains the existing resume checkpoint. Executors implementing
this third-argument ABI advertise `javascript.portable.v1` alongside `javascript`.
Jobs using `ctx` require that additional capability, so an older `javascript`
executor cannot claim them during a rolling update. Network JS additionally
requires `http.request` and `artifact.stream`. Raw legacy two-argument jobs keep
requiring only `javascript`. The new third argument provides `ctx.checkpoint(value)`, `ctx.progress(value)` and synchronous
`ctx.http.request(request)`. Legacy global `checkpoint` and `progress` remain
aliases. A checkpoint is acknowledged only after durable local persistence;
progress is observation and is not a recovery checkpoint. Returned values and
checkpoints must be JSON values. Host credentials, filesystem, Java classes,
`require`, `process`, `Buffer`, browser globals, Promises and asynchronous JS are
outside this portable ABI. Cancellation is host enforced, including interpreted
computation and blocked network calls.

A Node adapter using Node `vm` is trusted-code execution, not a hostile-code
sandbox. It advertises `trusted_javascript` and opts into `javascript` explicitly.
PHP without a JS engine advertises `http.request`, not `javascript`. Native/GPU
operations require their own capabilities and host admission.

## HTTP operation and host function

The declarative operation `http.request` takes the same request object as
`ctx.http.request`. It lets a PHP host perform network work without a JS engine.
See [request schema](schemas/http-request.schema.json) and
[response schema](schemas/http-response.schema.json).

```json
{
  "effect_id": "download-source-v1",
  "url": "https://example.org/archive.zip",
  "method": "GET",
  "headers": {"Accept": "application/zip"},
  "replay": "safe",
  "response": {"mode": "artifact", "name": "archive.zip"}
}
```

A stable explicit `effect_id` is mandatory in JS. The declarative operation
supplies `request` when omitted. Never derive IDs from a transient call ordinal.
Method defaults to GET; replay defaults to `safe` for GET/HEAD and `never` for
other methods. `safe` is accepted only for GET/HEAD. `idempotent` requires a
nonempty vendor-supported `idempotency_key`, actually sent in the named
`idempotency_header` (default `Idempotency-Key`). A conflicting caller header is
rejected. The normalized full request sent to the effect journal contains that
header. Merely adding a header cannot make an arbitrary vendor idempotent.

`body_text` is UTF-8. Streaming an artifact as a request body is not in the
baseline capability: an executor must reject an unsupported field rather than
load an unbounded artifact into memory or pretend it was sent.

Absent caller headers, the host sends `Accept: */*`, `Accept-Encoding: identity`
and `User-Agent: DistributedWorker/1`. Invalid header names, CR/LF and embedded
URL credentials are rejected. Device API credentials are never attached to the
source request. Bytes mean the HTTP entity after transfer framing, before any
Content-Encoding decoding. Do not transparently decompress; preserve the
Content-Encoding response header. HTTP 4xx/5xx are HTTP responses, not transport
exceptions.

`redirect` is `follow` (default) or `manual`. Manual mode returns the original
redirect response without following it. Optional `timeouts.connect_ms` and
`timeouts.read_idle_ms` are nonnegative milliseconds; zero disables that timer.
They bound connection setup or actual source-read inactivity, never total job or
body duration. Deliberate gateway upload backpressure must not be mistaken for
source inactivity. No overall HTTP deadline is introduced.

The default response mode is `artifact`. `text` and `json` require explicit
nonnegative `max_bytes`: the caller chooses an inline allocation budget that must
fit the host's actual memory. Inline text is strict UTF-8; JSON is parsed only
after complete UTF-8 validation. Overflow, invalid UTF-8 or invalid JSON returns
the **full** sealed artifact with `body_error.code` respectively
`inline_limit_exceeded`, `invalid_utf8` or `invalid_json`; no bytes are truncated
or discarded. Both declarative and JS operations return that metadata normally.
A zero-byte inline budget accepts an empty text body and otherwise selects the
full artifact fallback; an empty JSON body remains invalid JSON. The caller may
inspect the metadata and explicitly fail its business operation.

```json
{
  "status": 200,
  "headers": {"content-type": ["application/zip"]},
  "url": "https://example.org/archive.zip",
  "artifact": {
    "artifact_id": "opaque-id", "name": "archive.zip", "length": 123456,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "media_type": "application/zip"
  }
}
```

For inline success, `body_text` or `body_json` carries the value; an optional
`artifact` may pin the complete source bytes for durable recovery. Header names
are lower-case and values are arrays, preserving repeated fields. A fallback
includes `artifact` and `body_error` together. Streams use bounded buffers and
backpressure; binary bodies never pass through JS byte arrays or queue JSON.

### Control-envelope capacity

The gateway accepts a 16 MiB UTF-8 JSON control envelope. This bounds API control
messages, not artifact bytes or the runtime of a job. Measure the complete
serialized envelope, including identity, metadata, escaping and artifact IDs.
A response whose raw bytes fit an inline budget may still exceed this envelope.
If complete HTTP source bytes are already pinned, remove inline body fields and
return their full artifact plus `body_error.code = control_budget_exceeded`.
Never silently crop text, JSON fields or computed values to fit.

An oversized general computation result is represented as:

```json
{"artifact":{"artifact_id":"opaque-id","name":"result.json","length":20000000,
"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
"media_type":"application/json"},"result_format":"json",
"body_error":{"code":"control_budget_exceeded"}}
```

The artifact holds UTF-8 JSON of the original **result value**, not the completion
envelope. Stream serialization/upload with bounded buffers, retain its SHA and
durable upload offset, and include its ID in completion `artifact_ids`. A changed
recomputed result uses a different content-bound upload key; it cannot append to
old bytes. Lost chunk/seal acknowledgements reconcile through upload status.
Metadata is cleared only after a matching durable completion acknowledgement.
If metadata alone cannot fit the actual API capacity, retain the result artifact
and report that concrete transport constraint instead of deleting data.

### Optional network policy and redirects

Admin enqueue is privileged. A specified URL may legitimately use HTTP or a
private address. An absent `job.network` adds no blanket LAN/HTTP prohibition.
An optional policy is an allow-policy checked before every request/redirect:

```json
{"network": {
  "origins": ["https://example.org"], "methods": ["GET", "HEAD"],
  "request_headers": ["accept", "accept-encoding", "user-agent"],
  "response_modes": ["artifact", "text", "json"]
}}
```

Omitted dimensions are unrestricted; an explicit empty array denies that
dimension. Compare normalized scheme/host/effective-port origins, never a
substring, host suffix or path prefix. Header allowance is case-insensitive and
covers effective headers including host defaults and injected idempotency keys.
TLS verification stays enabled. Redirect loops are diagnosed from visited
request states, not an arbitrary count. Method rewriting follows HTTP semantics;
remove entity headers when rewriting to GET. Same-origin redirects preserve
applicable caller headers. Cross-origin redirects are followed only when the
resulting method is GET/HEAD, and strip caller headers except Accept,
Accept-Encoding, User-Agent and Content-Type. A cross-origin redirect preserving
an unsafe method returns the original redirect response/Location/body; the caller
must create a new explicit request/effect ID for the other origin. This avoids
forwarding secrets or silently stripping a vendor idempotency guarantee.

## Durable external-effect journal

Journal external HTTP intent **before** contacting the source. The gateway
normalizes the full original request (including defaults and effective headers)
and returns its fingerprint. Clients must not independently canonicalize/hash
JSON across Java, Node and PHP.

`POST /v1/jobs/{job}/effects/{effect_id}/begin`:

```json
{
  "worker_id": "worker-example", "attempt_id": "00000000-0000-4000-8000-000000000001",
  "fencing_token": 1, "execution_id": "00000000-0000-4000-8000-000000000002",
  "request": {"effect_id":"download-source-v1","url":"https://example.org/archive.zip","method":"GET","replay":"safe"}
}
```

Response: `{state:execute|completed|uncertain,effect_id,execution_id,request_sha256,response?,artifact_ids?}`.

| Durable state / caller | Decision |
| --- | --- |
| First reservation | `execute` |
| Same owner and same live-call execution UUID retry | `execute` |
| Already completed, matching request | `completed`, cached response and artifact IDs |
| Started `never`, different execution UUID | `uncertain`; do not contact source |
| Started safe / vendor-idempotent takeover | New reserved execution UUID may execute |
| Same effect ID, different normalized request | 409 conflict |

Generate `execution_id` before the begin RPC. Reuse it for an uncertain begin
transport retry only in that same live call before dispatching the source
request. After process restart/new host call generate a new UUID, even within
the same job attempt. An `execute` reply does not authorize repeating a source
request whose own transport outcome became uncertain.

`POST /v1/jobs/{job}/effects/{effect_id}/finish` body:
`{worker_id,attempt_id,fencing_token,execution_id,request_sha256,response,artifact_ids}`.
Use the hash returned by begin. ACK:
`{effect_id,execution_id,recorded:true,candidate_saved?:true}`. Finish must match
the reserved nonce; an old response cannot overwrite a newer safe execution.
Record the response and sealed artifact IDs durably before returning to JS or
completing the job. A late eligible owner response can be retained without
committing the job. Reconcile uncertain finish RPCs through status while the assignment is live,
or repeated identical finish writes after expiry, not another source call.
A cached begin response also requires a live assignment.

`GET /v1/jobs/{job}/effects/{effect_id}/status` returns
`{state:started|uncertain|completed,effect_id,execution_id,request_sha256,response?,artifact_ids?}`.
`started` is evidence of durable intent, not permission to redispatch. Only begin
can grant `execute`.

## Streaming binary artifacts

Artifacts live in gateway durable storage. Queue events and completions carry
small references. This deliberately uses server storage; it does not promise
that a large binary is literally held inside RabbitMQ or that persistence is
free. Consumers stream bytes through the gateway API.

| Route | Body / response |
| --- | --- |
| `POST /v1/jobs/{job}/artifacts` | `{worker_id,attempt_id,fencing_token,key,media_type,name?}` → 201 new /200 replay `{upload_id}` |
| `GET /v1/artifacts/{id}/status` | `{offset,status,last_segment?,segments_count,lease_until,cancel_requested,artifact?}` |
| `PUT /v1/artifacts/{id}/chunks/{offset}` | Raw HTTP body → `{upload_id,offset,next_offset,length,sha256}` |
| `POST /v1/artifacts/{id}/finish` | `{worker_id,attempt_id,fencing_token,length,sha256?}` → sealed artifact reference with `committed,candidate_saved` |
| `POST /v1/artifacts/{id}/abort` | `{worker_id,attempt_id,fencing_token}` → soft-close; acknowledged segments retained |
| `GET /v1/artifacts/{artifact_id}/content` | Device bearer + identity headers; scoped sealed-artifact recovery stream |
| `GET /v1/admin/artifacts/{artifact_id}/content` | Admin bearer only; streams exact bytes with media type, attachment disposition and SHA ETag |

Status/content GET and raw chunk PUT use device bearer plus `X-Worker-Id`,
`X-Attempt-Id`, `X-Fencing-Token`. JSON routes carry identity in their bodies.
The device content route requires the current live fenced attempt of the
**same job**. Cross-job reads are unauthorized (401); an expired or replaced
attempt loses its read access (409), including the original creator. A
missing/unsealed artifact is 404. This route recovers a sealed inline response
after a process restart; it does not advertise streaming input request bodies.
Admin content remains admin-only. Content is streamed byte-for-byte with SHA
ETag and validated Range support.

`key` is opaque metadata, never a filesystem path; use a stable
`effect_id/execution_id` per source execution. Create is idempotent for the same
attempt/key. Chunks accept Content-Length or chunked transfer. The gateway copies
with bounded buffers, hashes incrementally, and acknowledges only after file
fsync and database commit. Repeating the same offset/bytes returns the same ACK;
different bytes at that offset conflict. Status `offset` is total durable bytes;
`last_segment` is `{offset,length,sha256}`. Do not return the entire segment list.

Finish validates length and optional whole hash before sealing; the server
computes the full hash. A stale authorized upload may be retained as candidate,
while current job completion controls canonical visibility. Finish alone does
not finish the job. 400 means invalid, 401 unauthorized ownership, 404 missing,
409 conflicting/closed/offset/hash state. Preserve acknowledged offsets after
network/storage failures. No default full-artifact byte cap is part of v1.

Source GET resume requires a strong validator and exact validated Content-Range.
If the source ignores Range or changes identity, never append its full body to
old bytes. Preserve the old candidate and start a new safe execution/key from
zero. Non-idempotent source requests cannot be reissued merely to rebuild an
interrupted body. A PHP request can yield at a host-enforced request boundary
only when a durable checkpoint and safe source-resume proof exist; otherwise
report the actual source/hosting limitation and retain partial bytes.

## Failure and recovery sequence

1. Claim → durable local current job/checkpoint; renew lease independently.
2. Effect begin → source dispatch once under that reservation.
3. Stream → durable chunk ACKs → artifact seal → durable effect finish.
4. Persist completion outbox → send completion + artifact IDs → matching receipt.
5. If disconnected, retain checkpoint/outbox. If lease lost, old results are
   candidates; the current attempt alone commits canonical results.

A read retry is not a blind repeat of an uncertain write. `never` effects with
ambiguous external outcomes require inspection or an explicitly new authorized
effect, while safe requests can be resumed/reexecuted according to validators.
Cancellation, storage errors and worker disappearance are separate observations;
a healthy but long source stream is not a stalled job simply because no new
queue message has appeared.

## Android release compatibility

Android release metadata remains RSA-signed with package, increasing version,
APK hash and pinned installed signing certificate checks. Provider fallback is
Gitea → GitFlic → GitHub, configured privately at runtime. Public source/APKs
contain only safe template defaults/public repository identities; actual private
infrastructure endpoints and tokens are excluded. The bootstrap legacy
`/releases/android-stable/manifest.json` route is deployment migration support,
not a fourth provider in the new discovery chain. Release signature bytes and
verification are owned by the Android component contract.

For legacy manifest verification, metadata is
`{package_name,version_code,version_name,url,sha256,apk_certificate_sha256,signature_alg:"SHA256withRSA",signature:"base64"}`.
The pinned RSA public DER verifies the exact UTF-8 sequence, including the final
newline:

```text
package_name + "\n" + version_code + "\n" + version_name + "\n" + url + "\n" + sha256 + "\n" + apk_certificate_sha256 + "\n"
```

The package is `local.hwak.fleetworker`. Package/certificate must match the
installed application and only a greater version code installs. Gradle version
properties remain `workerVersionCode` and `workerVersionName`.

## Opt-in native command execution

`command.run` is a generic backend for an operator-provisioned program. It is not
an arbitrary shell, executable download or filesystem path supplied by a task.
An implementation advertises `command.run.v1` only while at least one configured
program is usable, along with each usable program's operator-chosen version
capability. Jobs require `command.run.v1`, `artifact.stream` and the exact program
version capability. These capabilities identify configured runtime support;
there is no catalog of application-specific operations in this protocol.

```json
{"operation":"command.run",
 "input":{"program":"transform-v1","payload":{"source":"https://example.org/input"}},
 "required_capabilities":["command.run.v1","artifact.stream","program.transform.v1"]}
```

The private operator configuration maps that opaque ID to an absolute executable,
fixed argv, exact SHA-256, explicit fixed environment values and version
capability. The executor verifies usability and hash before advertisement and
again immediately before launch. Deployment must keep executable/dependency paths
operator-controlled and immutable during a launch; a hash check is not a hostile
local filesystem attestation. The program is launched directly without a shell.
Only `payload` is serialized to stdin as UTF-8 JSON; worker IDs, tokens, gateway
configuration and the task envelope are not added to it. Environment inheritance
is disabled, apart from Windows SystemRoot; dependency paths must be explicitly
configured. Program provisioning and cross-compilation belong to its owner.

Raw stdout is opaque application data. Stream it through bounded buffers and
backpressure to a durable artifact, without a full-body buffer or local payload
file. Return [command result metadata](schemas/command-result.schema.json):

```json
{"artifact":{"artifact_id":"opaque-id","name":"stdout.bin","length":123,
 "sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
 "media_type":"application/octet-stream"},"exit_code":0,
 "stderr":{"tail_base64":"","bytes":0,"retained_bytes":0,"truncated":false}}
```

Nonzero exit, process signal or execution failure produces a failed completion
while retaining stdout when it can be sealed. Stderr is a separately bounded
byte tail with total/retained counts and an explicit truncation flag; it is not
mixed into stdout or printed in worker logs. Cancellation/lease loss kills the
child process tree and preserves acknowledged output. A lost chunk or finish
ACK repeats the same idempotent write. Durable stdout/hash/exit metadata can seal
an already completed execution after restart; a sealed result is reused before
spawning another process. Command metadata is removed only after the matching
completion ACK, or retained with a quarantined completion.

A private program's replay policy is `never` by default. If execution was started
but no complete durable output/exit record exists, a restart reports
`command_outcome_uncertain`; it must not silently repeat possible side effects.
An operator may declare a genuinely repeatable program `safe`. Its restart uses
a new retained upload generation and reruns the same independently idempotent
unit; never append fresh stdout to an older partial output. This is not exactly
once process execution. Native network calls do not use the HTTP host effect
journal, and an operator declaration cannot make an unsafe vendor idempotent.

This baseline has no native checkpoint/progress side channel or cross-host
process checkpoint migration. Use independent resumable units and application
idempotency instead of one monolithic campaign per process. Long jobs keep their
lease and have no added total runtime deadline. Native programs can access their
OS account's files and network; a child process is not a security sandbox.
Nonempty `job.network` must be rejected because this backend cannot enforce the
host HTTP allow-policy inside a native process. Real isolation requires an
operator-provisioned OS/container boundary. Node is the initial backend; Android
and PHP must not advertise this capability without an implemented native runner.
