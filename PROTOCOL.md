# Bootstrap protocol v1

API base is configured (HTTPS, may contain a path prefix). Endpoints are relative to it.
Device requests use `Authorization: Bearer <per-device token>`. Never log token.
`worker_id` is configured during bootstrap (e.g. worker-android-01).

POST /v1/workers/heartbeat body {worker_id, version_code, version_name, capabilities:[], abis:[], state, active_job_id?}; 200.
POST /v1/jobs/claim body {worker_id,capabilities:[]}; 204 when empty, otherwise
{job:{id,operation,input:{},script?:string,attempt_id,fencing_token,lease_until}}.
Operations for first real pilot: sha256(input.text), text.normalize(input.text), javascript(script function run(input) -> JSON).
POST /v1/jobs/{id}/heartbeat body {worker_id,attempt_id,fencing_token,progress:{}}; 200 renewable lease.
POST /v1/jobs/{id}/complete body {worker_id,attempt_id,fencing_token,status:succeeded|failed,result:any,error?:{code,message}}.
200 {job_id,attempt_id,committed:true}. Repeated same completion gets same ACK.
Stale completion is persisted as candidate and gets 200 {job_id,attempt_id,committed:false,candidate_saved:true}.
Client durable outbox removes only after matching committed or candidate_saved receipt.
No default overall job deadline, no fixed retry-attempt exhaustion. Leases detect lost workers, not healthy long jobs.

GET /releases/android-stable/manifest.json no device secret required:
{package_name,version_code,version_name,url,sha256,apk_certificate_sha256,signature_alg:"SHA256withRSA",signature:"base64"}.
The RSA release public DER is pinned in Android assets/release-public.der.
Signed bytes UTF-8 exact:
package_name + "\n" + version_code + "\n" + version_name + "\n" + url + "\n" + sha256 + "\n" + apk_certificate_sha256 + "\n".
APK package and certificate must match the installed worker; only greater version_code installs.
Package name: local.hwak.fleetworker. Initial 0.1.0/code1, real update proof 0.1.1/code2.
Version Gradle properties workerVersionCode and workerVersionName.
Admin endpoint/CLI uses a separate admin token to enqueue and inspect jobs; never distribute to APK.
