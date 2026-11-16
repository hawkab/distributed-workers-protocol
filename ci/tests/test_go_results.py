"""Go JSON events distinguish skipped tests from packages with no tests."""
import importlib.util
import json
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "fleetci.py"
spec = importlib.util.spec_from_file_location("fleetci_go_results", MODULE)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def events(*values):
    return "\n".join(json.dumps(value) for value in values)


class GoResultTests(unittest.TestCase):
    def test_cli_packages_without_test_files_do_not_fail_executed_tests(self):
        raw = events({"Action":"pass","Package":"fleet/gateway","Test":"TestActualDatabase"},
                     {"Action":"pass","Package":"fleet/gateway"},
                     {"Action":"skip","Package":"fleet/gateway/cmd/fleetctl"})
        self.assertEqual(len(ci.check_go_results(raw)),3)

    def test_named_skipped_integration_test_still_fails_release(self):
        with self.assertRaisesRegex(ci.Failure,"gateway_tests_failed_or_skipped"):
            ci.check_go_results(events({"Action":"skip","Package":"fleet/gateway","Test":"TestActualDatabase"}))

    def test_package_failure_is_never_ignored(self):
        with self.assertRaisesRegex(ci.Failure,"gateway_tests_failed_or_skipped"):
            ci.check_go_results(events({"Action":"fail","Package":"fleet/gateway"}))

    def test_nonzero_go_exit_fails_even_after_partial_passing_events(self):
        with self.assertRaisesRegex(ci.Failure,"gateway_tests_failed_or_skipped"):
            ci.check_go_results(events({"Action":"pass","Test":"TestEarlier"}),1)

    def test_no_actual_tests_cannot_be_reported_as_success(self):
        with self.assertRaisesRegex(ci.Failure,"gateway_tests_not_executed"):
            ci.check_go_results(events({"Action":"skip","Package":"fleet/gateway/cmd/fleetctl"}))

    def test_diagnostic_output_preserves_error_and_redacts_secrets(self):
        output=ci.sanitize_output(b"DB failure token=example-sensitive-token count=17",{"GITFLIC_TOKEN":"example-sensitive-token"})
        self.assertEqual(output,"DB failure token=<redacted> count=17")


class PrivateConfigurationLogTests(unittest.TestCase):
    def test_release_configuration_urls_and_hosts_are_redacted(self):
        config=json.dumps([{"id":"gitea","url":"https://private.example.invalid/git"}])
        text=ci.sanitize_output("Connection failed for private.example.invalid",{"FLEET_RELEASE_SOURCES_JSON":config})
        self.assertEqual(text,"Connection failed for <redacted>")
