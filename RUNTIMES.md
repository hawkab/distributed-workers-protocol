# Runtime choices for the v1 portable ABI

Reviewed against primary documentation on 2026-11-16. These are compatibility
constraints and design conclusions, not claims that every adapter/device has
passed deployment acceptance.

| Host | Practical executor | Advertised work |
| --- | --- | --- |
| Linux / Windows with Node | Native worker supervising a separate trusted JS process | Built-ins, `http.request`; `javascript` + `trusted_javascript` only after operator opt-in |
| Android, including ARM32 | Existing interpreted Rhino inside APK; host supplies network/stream bridge | Built-ins, portable JS and HTTP once that version is implemented and tested |
| PHP hosting without a JS engine | Request/cron-driven PHP adapter | Declarative `http.request`; optional configured native `command.run`; no JS capability |
| GPU / browser / vendor-native tools | Separate capability-specific adapter | Only its actual supported operations |

The shared instruction is ES5.1 **source**, JSON input/checkpoint/result and a
small host ABI. It is not Node-specific JavaScript or engine-specific bytecode.
This keeps `run(input,resume,ctx)` reusable while the host handles sockets,
credentials, cancellation, durable outbox and binary uploads.

## Node

Node explicitly states that `node:vm` must not execute untrusted code. A
separate process with an emptied environment reduces accidental credential
exposure but is not by itself an adversarial sandbox. The Node adapter therefore
requires trusted-code opt-in; stronger isolation needs an OS/container boundary
and its own acceptance checks. [Official VM documentation](https://nodejs.org/api/vm.html#vm-executing-javascript)

Use a spawned process and a bounded control channel, with the parent streaming
source bytes to the gateway. Respect `write()` backpressure/drain or a stream
pipeline. Avoid `exec`/`execFile` buffered output for binary results: the documented
`maxBuffer` can terminate and truncate a subprocess. These are host implementation
choices, not extra globals exposed to portable scripts.
[Streams](https://nodejs.org/api/stream.html),
[child processes](https://nodejs.org/api/child_process.html)

## Android alternatives

Rhino is Java implementation source, and its `ClassShutter` filters Java classes
exposed through LiveConnect. A restricted embedding must deny Java visibility,
avoid wrapping privileged Java objects, supply only narrow JSON host functions
and enforce cancellation through the interpreter instruction observer. Android
uses interpreted mode rather than generated JVM bytecode. Class filtering alone does not
prove that an arbitrary embedding is a complete hostile-code sandbox.
[Mozilla repository](https://github.com/mozilla/rhino),
[ClassShutter source](https://github.com/mozilla/rhino/blob/master/rhino/src/main/java/org/mozilla/javascript/ClassShutter.java),
[Rhino interpreter/observer API](https://github.com/mozilla/rhino/blob/master/rhino/src/main/java/org/mozilla/javascript/Context.java)

Jetpack JavaScriptEngine evaluates without a WebView instance and connects to an
out-of-process engine. It is available on API26+ **only when the installed WebView
implementation supports it**; `JavaScriptSandbox.isSupported()` is mandatory.
Consequently API level or ARM32 alone cannot justify advertising that engine on
a TV. Replacing a proven Rhino executor would require a real device availability
probe and bridge/cancellation tests first.
[Android JavaScriptEngine guide](https://developer.android.com/develop/ui/views/layout/webapps/jsengine)

QuickJS offers embedding hooks for memory/stack limits and interrupts. It is a
reasonable future native-runtime candidate, but Android JNI/NDK artifacts need
per-ABI builds and tests. Its bytecode is tied to a QuickJS version and is not
validated for untrusted loading, so it is unsuitable as this wire protocol's
portable instruction format. [QuickJS manual](https://bellard.org/quickjs/quickjs.html#QuickJS-C-API)

## PHP hosting

The PHP HTTP stream wrapper depends on `allow_url_fopen`; HTTPS additionally
requires OpenSSL. Discover available transports/extensions and advertise only
what is usable. A hosting account with neither working HTTPS streams nor cURL
cannot act as an HTTPS pull worker without another supported transport. Do not
claim installing a JavaScript engine is possible on a restricted shared host.
[HTTP wrapper](https://www.php.net/manual/en/wrappers.http.php)

HTTP contexts support method, headers and request content. Disable automatic
redirects, fetch HTTP error bodies, and apply the shared redirect policy manually.
The documented socket read timeout is distinct from the whole job lifespan.
[HTTP context options](https://www.php.net/manual/en/context.http.php)

With cURL, write callbacks can forward chunks and
`CURLOPT_HTTP_CONTENT_DECODING=false` preserves encoded entity bytes. A stream-only
adapter can hold one upload chunk at a time. Neither needs a body-wide temporary
file. `php://temp` **can spill to disk**, so using it would not substantiate a
zero-local-disk claim.
[cURL constants](https://www.php.net/manual/en/curl.constants.php),
[PHP streams](https://www.php.net/manual/en/wrappers.php.php)

PHP execution limits and any hosting FPM request termination must be measured
from the actual hosting configuration. Persist progress and yield between
requests when safe; do not add a synthetic overall job deadline or pretend that
changing PHP's own timer removes a hosting-level limit.
[PHP execution timer](https://www.php.net/manual/en/function.set-time-limit.php),
[FPM request termination configuration](https://www.php.net/manual/en/install.fpm.configuration.php)

## HTTP recovery rationale

The protocol deliberately narrows safe automatic replay to GET/HEAD. Ambiguous
non-idempotent requests need an explicit vendor idempotency contract or an
uncertain-effect state. Partial bodies can only be combined with a common strong
validator and verified ranges; a server may ignore Range and return a full body.
Those facts motivate the durable journal and upload generations rather than
blind retries. [HTTP semantics, RFC9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)

Optional PHP native commands use array-form `proc_open` without a shell,
nonblocking pipes and an explicit private environment. Function availability and
the executable hash determine advertisement; unavailable process functions do
not disable unrelated compute/HTTP work. POSIX descendant cleanup uses checked
process birth identities where available; otherwise only the direct process can
be forcibly terminated. The configured program must supervise its dependencies.
[PHP process execution](https://www.php.net/manual/en/function.proc-open.php),
[PHP process termination](https://www.php.net/manual/en/function.proc-terminate.php).
