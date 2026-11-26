#!/usr/bin/env python3
"""Run real queued examples; retain private resumable receipts and export only metrics."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("sha256", "word-count", "release-download", "mirror-download")


def save(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def milliseconds(start, end):
    return round((datetime.datetime.fromisoformat(end.replace("Z", "+00:00")) -
                  datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() * 1000, 3)


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["scenario"], []).append(row)
    result = {}
    for name, samples in groups.items():
        group = {"trials": len(samples), "passed": sum(x["passed"] for x in samples)}
        for key in ("queue_ms", "execution_to_result_ms", "server_total_ms", "observed_total_ms"):
            values = sorted(x[key] for x in samples if x.get(key) is not None)
            if values:
                group[key] = {"median": statistics.median(values), "min": values[0], "max": values[-1]}
        result[name] = group
    return result


def export_report(config, state, plan, output):
    count = len(plan["scenarios"]) * plan["repetitions"]
    report = {"schema": 1, "label": config.get("label", "mixed-pool"),
              "report_generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "expected_trials": count, "complete": len(state["samples"]) == count,
              "example_sha256": plan["example_sha256"], "samples": state["samples"],
              "summary": summarize(state["samples"]),
              "passed": all(x["passed"] for x in state["samples"])}
    save(output, report)
    return report


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, base, token):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid API base")
        self.base, self.token = base.rstrip("/"), token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, method, path, value=None, wait=False, content=False):
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        if wait:
            headers["Prefer"] = "wait=20, return=minimal"
        request = urllib.request.Request(self.base + path, method=method, headers=headers,
                                         data=None if value is None else json.dumps(value).encode())
        pause = 1
        while True:
            try:
                # Socket inactivity only; this does not limit job age or total transfer time.
                with self.opener.open(request, timeout=60) as response:
                    if content:
                        count, digest = 0, hashlib.sha256()
                        while True:
                            block = response.read(65536)
                            if not block:
                                break
                            count += len(block)
                            digest.update(block)
                        return {"length": count, "sha256": digest.hexdigest()}
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504):
                    raise RuntimeError("gateway HTTP " + str(error.code)) from None
                retry = error.headers.get("Retry-After", "")
                error.close()
                delay = float(retry) if retry.isdigit() else pause
            except (OSError, urllib.error.URLError):
                delay = pause
            # Retry uncertain enqueue with the same saved idempotency key.
            time.sleep(delay)
            pause = min(pause * 2, 20)


def verify(api, scenario, job, expected):
    result = job.get("result") or {}
    value = result.get("result") or {}
    if job["status"] != "succeeded" or result.get("status") != "succeeded":
        return False, {"failure": "executor_failed"}
    if scenario not in ("release-download", "mirror-download"):
        return value == expected[scenario], {"value": value} if value == expected[scenario] else {"failure": "value_mismatch"}
    artifact = value.get("artifact") or {}
    if value.get("status") != 200 or not artifact.get("artifact_id"):
        return False, {"failure": "source_response"}
    begun = time.monotonic()
    observed = api.call("GET", "/v1/admin/artifacts/" + urllib.parse.quote(artifact["artifact_id"], safe="") + "/content", content=True)
    good = observed["sha256"] == expected[scenario]["sha256"] == artifact.get("sha256") and observed["length"] == artifact.get("length")
    return good, {"bytes": observed["length"], "sha256": observed["sha256"],
                  "artifact_verified": good, "verification_ms": round((time.monotonic() - begun) * 1000, 3)}


def execute(config, state_file, output, scenarios, repetitions):
    api = API(config["base_url"], Path(config["admin_token_file"]).read_text().strip())
    expected = json.loads((ROOT / "examples/benchmark/expected.json").read_text())
    specs = {name: json.loads((ROOT / "examples" / (name + ".json")).read_text()) for name in scenarios}
    plan = {"scenarios": scenarios, "repetitions": repetitions, "config": config,
            "example_sha256": {name: hashlib.sha256((ROOT / "examples" / (name + ".json")).read_bytes()).hexdigest() for name in scenarios}}
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    state = json.loads(state_file.read_text()) if state_file.exists() else {
        "run": uuid.uuid4().hex, "plan_sha256": fingerprint, "jobs": {}, "samples": []}
    if state["plan_sha256"] != fingerprint:
        raise ValueError("existing state belongs to a different plan; choose another state file")
    save(state_file, state)
    for scenario in scenarios:
        for trial in range(1, repetitions + 1):
            key = scenario + ":" + str(trial)
            if any(x["scenario"] == scenario and x["trial"] == trial for x in state["samples"]):
                continue
            resumed = key in state["jobs"]
            entry = state["jobs"].setdefault(key, {"idempotency_key": "example:" + state["run"] + ":" + key})
            save(state_file, state)
            begun = time.monotonic()
            if "job_id" not in entry:
                spec = json.loads(json.dumps(specs[scenario]))
                spec["required_capabilities"] = sorted(set(spec.get("required_capabilities", []) + config.get("required_capabilities", [])))
                spec["resource_requirements"] = config.get("resource_requirements", {})
                spec["priority"] = config.get("priority", 0)
                spec["idempotency_key"] = entry["idempotency_key"]
                entry["job_id"] = api.call("POST", "/v1/admin/jobs", spec)["job_id"]
                save(state_file, state)
            path = "/v1/admin/jobs/" + entry["job_id"]
            while True:
                job = api.call("GET", path)
                save(state_file.parent / (entry["job_id"] + ".private.json"), job)
                if job["status"] in ("succeeded", "failed", "cancelled"):
                    break
                api.call("POST", "/v1/admin/jobs/wait", {"ids": [entry["job_id"]]}, wait=True)
            elapsed = round((time.monotonic() - begun) * 1000, 3)
            result = job.get("result") or {}
            attempt = next((x for x in job.get("attempts", []) if x["id"] == result.get("attempt_id")), None)
            allowed = config.get("allowed_worker_ids", [])
            if allowed and attempt and attempt["worker_id"] not in allowed:
                raise RuntimeError("execution outside the configured measurement pool; private receipt retained")
            passed, details = verify(api, scenario, job, expected)
            versions = api.call("GET", "/v1/admin/workers")["workers"]
            worker = next((x for x in versions if attempt and x["id"] == attempt["worker_id"]), {})
            sample = {"scenario": scenario, "trial": trial, "operation": specs[scenario]["operation"],
                      "status": job["status"], "passed": passed, "resumed": resumed,
                      "submitted_at": job["created_at"], "finished_at": job["finished_at"],
                      "executor_version": worker.get("version_name"),
                      "observed_total_ms": None if resumed else elapsed,
                      "server_total_ms": milliseconds(job["created_at"], job["finished_at"]),
                      "queue_ms": milliseconds(job["created_at"], attempt["started_at"]) if attempt else None,
                      "execution_to_result_ms": milliseconds(attempt["started_at"], result["created_at"]) if attempt else None,
                      **details}
            state["samples"].append(sample)
            save(state_file, state)
            report = export_report(config, state, plan, output)
            print(json.dumps({"label": report["label"], "scenario": scenario, "trial": trial,
                              "passed": passed, "execution_to_result_ms": sample["execution_to_result_ms"]}), flush=True)
    export_report(config, state, plan, output)
    return all(x["passed"] for x in state["samples"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True, help="private resumable state; keep outside Git")
    parser.add_argument("--output", type=Path, required=True, help="sanitized measurements without endpoints or identities")
    parser.add_argument("--scenario", choices=SCENARIOS, action="append")
    parser.add_argument("--repetitions", type=int, default=5, help="finite experimental sample size, not a task retry budget")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    try:
        good = execute(json.loads(args.config.read_text()), args.state, args.output,
                       list(dict.fromkeys(args.scenario or ["sha256"])), args.repetitions)
    except (RuntimeError, ValueError, KeyError, OSError):
        parser.exit(1, "Example run needs attention; private state retained. Check configuration and canonical receipts.\n")
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted; queued work and private state retained. Repeat the same command to resume.\n")
    raise SystemExit(0 if good else 1)


if __name__ == "__main__":
    main()
