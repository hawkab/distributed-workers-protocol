import contextlib
import copy
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("examples_runner", ROOT / "tools/run-examples.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@contextlib.contextmanager
def fixture():
    state = {"jobs": {}, "creates": 0, "leaked": 0, "fail_trial": None, "binary": b"streamed fixture" * 10000}
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value, code=200):
            raw = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/admin/jobs":
                key = value["idempotency_key"]
                if key not in state["jobs"]:
                    state["creates"] += 1
                    state["jobs"][key] = {"id": "job-" + str(state["creates"]), "trial": state["creates"]}
                self.send({"job_id": state["jobs"][key]["id"]}, 201)
            elif self.path == "/v1/admin/jobs/wait":
                self.send({"jobs": []})
            else:
                self.send({}, 404)

        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/leak")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/leak":
                state["leaked"] += 1
                self.send({})
            elif self.path == "/v1/admin/workers":
                self.send({"workers": [{"id": "private-device", "version_name": "0.8.4"}]})
            elif self.path.endswith("/content"):
                self.send_response(200)
                self.send_header("Content-Length", str(len(state["binary"])))
                self.end_headers()
                self.wfile.write(state["binary"])
            else:
                job = next(x for x in state["jobs"].values() if self.path.endswith("/" + x["id"]))
                failed = job["trial"] == state["fail_trial"]
                self.send({"id": job["id"], "status": "failed" if failed else "succeeded",
                           "created_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:03Z",
                           "attempts": [{"id": "private-attempt", "worker_id": "private-device", "started_at": "2026-01-01T00:00:01Z"}],
                           "result": {"attempt_id": "private-attempt", "status": "failed" if failed else "succeeded",
                                      "created_at": "2026-01-01T00:00:02.900Z", "result": {"sha256": hashlib.sha256(b"abc").hexdigest()}}})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class ExampleTests(unittest.TestCase):
    def test_live_evidence_matches_inputs_and_reconciles_every_outcome(self):
        evidence = json.loads((ROOT / "docs/benchmarks.json").read_text())
        expected = json.loads((ROOT / "examples/benchmark/expected.json").read_text())
        counts = {"successful": 0, "failed": 0, "interrupted": 0, "pending": 0}
        for scenario, digest in evidence["fixture_sha256"].items():
            self.assertEqual(hashlib.sha256((ROOT / "examples" / (scenario + ".json")).read_bytes()).hexdigest(), digest)
        for cohort in evidence["cohorts"]:
            samples = cohort["samples"]
            self.assertEqual(len(samples), cohort["started_trials"])
            self.assertEqual(cohort["started_trials"] + cohort["not_started_trials"], cohort["requested_trials"])
            self.assertEqual(len({x["trial"] for x in samples}), len(samples))
            for outcome, field in [("passed", "successful"), ("failed", "failed"), ("interrupted", "interrupted"), ("pending", "pending")]:
                observed = sum(x["outcome"] == outcome for x in samples)
                self.assertEqual(observed, cohort[field + "_trials"])
                counts[field] += observed
            for sample in samples:
                self.assertEqual(len(sample["receipt_sha256"]), 64)
                if sample["outcome"] != "passed":
                    self.assertNotIn("execution_to_result_ms", sample)
                    continue
                self.assertEqual(sample["server_total_ms"], runner.milliseconds(sample["submitted_at"], sample["finished_at"]))
                if cohort["scenario"] in ("release-download", "mirror-download"):
                    self.assertTrue(sample["artifact_verified"])
                    self.assertEqual(sample["sha256"], expected[cohort["scenario"]]["sha256"])
                    self.assertEqual(sample["bytes"], evidence["method"]["source_bytes"])
                else:
                    self.assertEqual(sample["value"], expected[cohort["scenario"]])
                for key in ("job_id", "worker_id", "attempt_id", "token", "base_url", "required_capabilities"):
                    self.assertNotIn(key, sample)
        self.assertEqual(counts, evidence["totals"])

    def configure(self, directory, base):
        token = directory / "token"
        token.write_text("private-fixture-secret")
        return {"base_url": base, "admin_token_file": str(token), "label": "fixture",
                "allowed_worker_ids": ["private-device"]}

    def test_real_http_runner_counts_failures_and_separates_queue_time(self):
        with tempfile.TemporaryDirectory() as temp, fixture() as (base, remote):
            directory = Path(temp)
            config = self.configure(directory, base)
            remote["fail_trial"] = 2
            with patch("builtins.print"):
                good = runner.execute(config, directory / "state.json", directory / "report.json", ["sha256"], 3)
            self.assertFalse(good)
            report = json.loads((directory / "report.json").read_text())
            self.assertTrue(report["complete"])
            self.assertEqual(report["summary"]["sha256"]["passed"], 2)
            self.assertEqual(report["summary"]["sha256"]["trials"], 3)
            self.assertEqual(report["samples"][0]["queue_ms"], 1000)
            self.assertEqual(report["samples"][0]["execution_to_result_ms"], 1900)
            self.assertEqual(report["samples"][0]["server_total_ms"], 3000)
            text = (directory / "report.json").read_text()
            for forbidden in [base, "private-device", "private-attempt", "job-1", "private-fixture-secret", str(directory)]:
                self.assertNotIn(forbidden, text)
            self.assertEqual((directory / "state.json").stat().st_mode & 0o777, 0o600)

    def test_lost_enqueue_reply_resumes_one_job_and_does_not_invent_client_timing(self):
        with tempfile.TemporaryDirectory() as temp, fixture() as (base, remote):
            directory = Path(temp)
            config = self.configure(directory, base)
            original = runner.API.call
            def interrupted(api, method, path, *args, **kwargs):
                value = original(api, method, path, *args, **kwargs)
                if method == "POST" and path == "/v1/admin/jobs":
                    raise KeyboardInterrupt()
                return value
            with patch.object(runner.API, "call", interrupted), self.assertRaises(KeyboardInterrupt):
                runner.execute(config, directory / "state.json", directory / "report.json", ["sha256"], 1)
            with patch("builtins.print"):
                self.assertTrue(runner.execute(config, directory / "state.json", directory / "report.json", ["sha256"], 1))
            self.assertEqual(remote["creates"], 1)
            sample = json.loads((directory / "report.json").read_text())["samples"][0]
            self.assertTrue(sample["resumed"])
            self.assertIsNone(sample["observed_total_ms"])
            (directory / "report.json").unlink()
            with patch.object(runner.API, "call", side_effect=AssertionError("closed jobs must not be repeated")):
                self.assertTrue(runner.execute(config, directory / "state.json", directory / "report.json", ["sha256"], 1))
            self.assertTrue(json.loads((directory / "report.json").read_text())["complete"])
            with self.assertRaisesRegex(ValueError, "different plan"):
                runner.execute(config, directory / "state.json", directory / "report.json", ["sha256"], 2)

    def test_api_redirect_cannot_forward_the_admin_credential(self):
        with fixture() as (base, remote):
            with self.assertRaisesRegex(RuntimeError, "302"):
                runner.API(base, "secret").call("GET", "/redirect")
            self.assertEqual(remote["leaked"], 0)

    def test_download_checks_full_bytes_and_both_hash_receipts(self):
        with fixture() as (base, remote):
            digest = hashlib.sha256(remote["binary"]).hexdigest()
            artifact = {"artifact_id": "private-artifact", "length": len(remote["binary"]), "sha256": digest}
            job = {"status": "succeeded", "result": {"status": "succeeded", "result": {"status": 200, "artifact": artifact}}}
            good, details = runner.verify(runner.API(base, "secret"), "mirror-download", job, {"mirror-download": {"sha256": digest}})
            self.assertTrue(good)
            self.assertEqual(details["bytes"], len(remote["binary"]))
            bad = copy.deepcopy(job)
            bad["result"]["result"]["artifact"]["sha256"] = "0" * 64
            self.assertFalse(runner.verify(runner.API(base, "secret"), "mirror-download", bad, {"mirror-download": {"sha256": digest}})[0])

    def test_wrong_pool_retains_private_evidence_and_does_not_publish_a_measurement(self):
        with tempfile.TemporaryDirectory() as temp, fixture() as (base, remote):
            directory = Path(temp)
            config = self.configure(directory, base)
            config["allowed_worker_ids"] = ["another-device"]
            with self.assertRaisesRegex(RuntimeError, "outside"):
                runner.execute(config, directory / "state.json", directory / "report.json", ["sha256"], 1)
            self.assertTrue((directory / "state.json").exists())
            self.assertFalse((directory / "report.json").exists())
