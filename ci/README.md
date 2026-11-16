# Component CI and releases

Native entry points are `gitflic-ci.yaml`, `.github/workflows/release.yml`,
and `.gitea/workflows/release.yml`. Version-tag pushes run their real platform
jobs; GitHub/Gitea manual dispatch retries an existing tag. GitFlic's UI/API can
rerun the pipeline for the same tag. No PR event receives publishing secrets.

GitFlic builds the canonical bundle. Other forges discover its exact tag/commit
and release record through anonymous public release HTML, verify
the component/commit/tag/checksums (plus the APK signing certificate and pinned
RSA manifest signature for Android), then attach the identical bytes. A pending
canonical release is polled with backoff, without an arbitrary attempt cap.
Native CI cancellation is resumable by dispatching the same version tag again.
`--once` reports pending with exit 75 for a nonblocking operator check.

GitFlic project variables: `FLEET_CI_IMAGE` (pinned toolchain image),
`GITFLIC_TOKEN` (API `Authorization: token`), optionally exact owner/project/API
overrides. Canonical owner/project defaults are in `component.json`.
`FLEET_GITFLIC_USERNAME` is the token owner's login, required by channel Git push
if its Git endpoint does not accept the default token username.

For Android also configure `FLEET_SIGNING_IMAGE`, `FLEET_PUBLIC_RELEASE_BASE`,
and the protected `fleetsigning` runner. The unsigned Gradle/test job runs on
`fleetci` without keys; its native artifact `dist/unsigned` feeds the signer.
The signer does NOT run Gradle. Mount APK/manifest keys read-only at
`/run/fleet-signing`: `apk-release.jks`, `apk-password`, `release-signing.pem`;
the existing APK alias is `fleet-worker`. Mount SDK at `/opt/android-sdk`, full
JDK21 at `/opt/java21`; set `ANDROID_HOME` and `JAVA_HOME` accordingly.
`FLEET_GPG_PRIVATE_KEY_FILE` points to the separate automation GPG key; it is
imported into an ephemeral GNUPGHOME only for signed channel commits. Neither
the user's GPG key nor SSH home is mounted. GitHub/Gitea receive no signing keys.

`ci/version.json` for Android is `{ "name": "0.2.0", "code": 4 }`; a tag must
match its name. Increment the positive monotonic code for later app releases;
never derive it from a platform-specific pipeline counter. The APK and signed
manifest names are `fleet-worker-android.apk` and `android-manifest.json`.

The signer publishes and anonymously downloads/verifies release files first,
then pushes a signed `release-channel` commit with `stable.json` containing
manifest_url, apk_url, canonical_record_url, tag, source_commit, version_code and
sha256. A newer channel is never downgraded. The APK's signed canonical URL is
not rewritten at a mirror. Set FLEET_PUBLIC_RELEASE_BASE to the immutable public
Gitea release directory, e.g.
https://github.com/hawkab/distributed-workers-android/releases/download/{tag}
The builder expands the literal {tag}, then appends /fleet-worker-android.apk.
It never adds the old /releases/android-stable suffix. The resolver's explicit
mirror APK URL downloads the same bytes without changing the canonical manifest.

Gitea uses its per-job `secrets.GITEA_TOKEN` with contents:write and a registered
`fleet-ci` runner. The mirror runner needs Python3/OpenSSL and, for Android,
JDK21 + SDK build-tools36.0.0 for apksigner/aapt; it needs no Go or private keys.
GitHub uses its per-job GITHUB_TOKEN on ubuntu-latest with pinned checkout and
installs the pinned APK verification tools. If hosted execution is unavailable,
replace runs-on with the registered self-hosted fleet-ci label and equivalent
toolchain, rather than reporting a YAML-only setup as working.

Gateway canonical tests start disposable PostgreSQL/RabbitMQ Docker containers
unless both test DSNs are explicitly supplied. A skipped Go test fails the
release. Set `FLEET_TEST_PG_IMAGE`/`FLEET_TEST_MQ_IMAGE` to pinned production-major
image digests for the actual runner. Only isolated test containers are removed.
Gateway bundles contain gateway/relay/fleetctl for linux amd64/arm64 and windows
amd64. Protocol releases require real tracked *.schema.json files with $schema;
platform releases package tracked deployment files without running them on a
production host. Archive ordering, ownership and timestamps are deterministic.

Publisher behavior: existing equal-hash assets are reused, changed assets and
release identities are rejected, uncertain writes are reconciled before retry.
No existing tag/asset is overwritten or deleted. release-record.json is uploaded
last and binds component, exact source commit, version tag and all artifact hashes.
The unit suite covers idempotence, checksum rejection, no overwrite, uncertain
upload recovery and a canonical release that remains pending beyond short retry
budgets. Actual remote successful CI runs are required separately.

## Private runtime configuration

Public Android builds never consume private endpoint settings. Gradle prepares
assets from the tracked safe example only: reserved gitea.invalid and the public
GitFlic/GitHub project identities. The build helper removes private locator and
publisher credential variables from the Gradle process environment. Signing and
mirror verification audit the uncompressed APK for unexpected source endpoints,
known job credentials and private-key material.

The owner applies the actual release chain after installation through the
DUMP-protected CONFIGURE_RELEASES operation, from an ignored local JSON file.
The values live only in the app's private preferences. They are never build
inputs and are not copied into public APKs, manifests or release records.

The signed canonical APK URL uses the public immutable GitHub release path.
FLEET_PUBLIC_RELEASE_BASE is optional and, when set, must equal that documented
GitHub base; infrastructure overrides fail before forming a public manifest.
