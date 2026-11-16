import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error

import jsonschema
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {p.name: json.loads(p.read_text()) for p in (ROOT / 'schemas').glob('*.schema.json')}
IDENTITY = {'worker_id': 'fixture-worker', 'attempt_id': '00000000-0000-4000-8000-000000000001', 'fencing_token': 1}
EXECUTION = '00000000-0000-4000-8000-000000000002'
ARTIFACT = {'artifact_id': 'opaque', 'name': 'file.bin', 'sha256': 'a' * 64, 'length': 123, 'media_type': 'application/octet-stream'}
RESPONSE = {'status': 200, 'headers': {'content-type': ['application/octet-stream']}, 'url': 'https://example.org/a', 'artifact': ARTIFACT}


def validate(name, value):
    schema = SCHEMAS[name + '.schema.json']
    registry = Registry().with_resources((name, Resource.from_contents(content)) for name, content in SCHEMAS.items())
    jsonschema.Draft202012Validator(schema, registry=registry, format_checker=jsonschema.FormatChecker()).validate(value)


class ContractTests(unittest.TestCase):
    def test_all_schemas_compile(self):
        for name, schema in SCHEMAS.items():
            with self.subTest(name=name):
                jsonschema.Draft202012Validator.check_schema(schema)

    def test_existing_compute_job_unchanged(self):
        validate('job', {'operation': 'javascript', 'input': {}, 'script': 'function run(input,resume){return resume;} '})

    def test_all_examples_validate(self):
        for path in (ROOT / 'examples').glob('*.json'):
            with self.subTest(path=path.name):
                validate('job', json.loads(path.read_text()))

    def test_new_ctx_example_excludes_legacy_only_executors(self):
        job = json.loads((ROOT / 'examples/javascript-http.json').read_text())
        self.assertEqual({'javascript', 'javascript.portable.v1', 'http.request', 'artifact.stream'}, set(job['required_capabilities']))
        validate('job', job)

    def test_old_completion_without_artifacts(self):
        validate('completion', dict(IDENTITY, status='succeeded', result={'value': 1}))

    def test_completion_artifacts_unique(self):
        value = dict(IDENTITY, status='succeeded', result={}, artifact_ids=['one'])
        validate('completion', value)
        value['artifact_ids'].append('one')
        with self.assertRaises(jsonschema.ValidationError):
            validate('completion', value)

    def test_network_optional_dimensions(self):
        validate('network-policy', {})
        validate('network-policy', {'origins': [], 'request_headers': [], 'methods': ['GET']})

    def test_network_typo_rejected(self):
        with self.assertRaises(jsonschema.ValidationError):
            validate('network-policy', {'allowed_hosts': ['example.org']})

    def test_http_defaults_allow_declarative_effect_omission(self):
        validate('http-request', {'url': 'http://example.org/a'})

    def test_json_inline_requires_explicit_budget(self):
        for mode in ('text', 'json'):
            with self.subTest(mode=mode):
                req = {'url': 'https://example.org/a', 'response': {'mode': mode}}
                with self.assertRaises(jsonschema.ValidationError):
                    validate('http-request', req)
                req['response']['max_bytes'] = 4096
                validate('http-request', req)

    def test_zero_inline_budget_and_optional_network_timers(self):
        request = {'url': 'https://example.org/a', 'redirect': 'manual',
                   'timeouts': {'connect_ms': 0, 'read_idle_ms': 120000},
                   'response': {'mode': 'text', 'max_bytes': 0}}
        validate('http-request', request)
        for change in ({'redirect': 'always'}, {'timeouts': {'read_idle_ms': -1}},
                       {'timeouts': {'overall_ms': 1000}}):
            with self.assertRaises(jsonschema.ValidationError):
                validate('http-request', dict(request, **change))

    def test_artifact_response_has_no_arbitrary_total_cap(self):
        response = copy.deepcopy(RESPONSE)
        response['artifact']['length'] = 10 ** 16
        validate('http-response', response)

    def test_idempotent_requires_key(self):
        req = {'url': 'https://example.org/a', 'method': 'POST', 'replay': 'idempotent'}
        with self.assertRaises(jsonschema.ValidationError):
            validate('http-request', req)
        req['idempotency_key'] = 'stable-vendor-key'
        validate('http-request', req)

    def test_safe_post_rejected(self):
        with self.assertRaises(jsonschema.ValidationError):
            validate('http-request', {'url': 'https://example.org', 'method': 'POST', 'replay': 'safe'})

    def test_header_injection_rejected(self):
        for headers in ({'Bad Header': 'x'}, {'Authorization': 'x\r\nInjected: yes'}):
            with self.subTest(headers=headers):
                with self.assertRaises(jsonschema.ValidationError):
                    validate('http-request', {'url': 'https://example.org/a', 'headers': headers})

    def test_unsupported_body_artifact_not_silently_accepted(self):
        with self.assertRaises(jsonschema.ValidationError):
            validate('http-request', {'url': 'https://example.org/a', 'body_artifact_id': 'not-baseline'})

    def test_fallback_full_artifact_with_error(self):
        for code in ('inline_limit_exceeded', 'invalid_utf8', 'invalid_json'):
            validate('http-response', dict(RESPONSE, body_error={'code': code}))

    def test_no_error_without_preserved_artifact(self):
        with self.assertRaises(jsonschema.ValidationError):
            validate('http-response', {'status': 200, 'url': 'https://example.org/a', 'headers': {}, 'body_error': {'code': 'invalid_json'}})

    def test_inline_json_null_is_valid_body(self):
        response = {key: value for key, value in RESPONSE.items() if key != 'artifact'}
        validate('http-response', dict(response, body_json=None))

    def test_no_mixed_inline_and_binary_body(self):
        with self.assertRaises(jsonschema.ValidationError):
            validate('http-response', dict(RESPONSE, body_text='truncated prefix'))

    def test_4xx_is_valid_response(self):
        validate('http-response', dict(RESPONSE, status=403))

    def test_response_duplicates_are_arrays(self):
        value = dict(RESPONSE, headers={'set-cookie': ['a=1', 'b=2']})
        validate('http-response', value)
        value['headers'] = {'Set-Cookie': ['a=1']}
        with self.assertRaises(jsonschema.ValidationError):
            validate('http-response', value)

    def test_effect_nonce_and_server_hash_contract(self):
        begin = dict(IDENTITY, execution_id=EXECUTION, request={'url': 'https://example.org/a'})
        validate('effect-begin', begin)
        with self.assertRaises(jsonschema.ValidationError):
            validate('effect-begin', dict(begin, request_sha256='a' * 64))
        finish = dict(IDENTITY, execution_id=EXECUTION, response=RESPONSE, artifact_ids=['opaque'])
        with self.assertRaises(jsonschema.ValidationError):
            validate('effect-finish', finish)
        validate('effect-finish', dict(finish, request_sha256='a' * 64))

    def test_begin_and_observation_states_differ(self):
        common = {'effect_id': 'e', 'execution_id': EXECUTION, 'request_sha256': 'a' * 64}
        validate('effect-begin-receipt', dict(common, state='execute'))
        validate('effect-status', dict(common, state='started'))
        with self.assertRaises(jsonschema.ValidationError):
            validate('effect-status', dict(common, state='execute'))

    def test_chunk_receipt_and_bounded_status(self):
        validate('artifact-chunk-receipt', {'upload_id': 'u', 'offset': 0, 'next_offset': 7, 'length': 7, 'sha256': 'a' * 64})
        status = {'offset': 7, 'status': 'open', 'segments_count': 1, 'cancel_requested': False,
                  'last_segment': {'offset': 0, 'length': 7, 'sha256': 'a' * 64}}
        validate('artifact-status', status)
        with self.assertRaises(jsonschema.ValidationError):
            validate('artifact-status', dict(status, segments=[status['last_segment']]))

    def test_fixture_config_check_is_offline_and_needs_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'api_base': 'https://example.invalid/fleet', 'admin_secret_file': '/nonexistent/secret'}))
            done = subprocess.run(['python3', '-B', str(ROOT / 'tools/probe-api.py'), '--config', str(config), '--check'], capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertNotIn('/nonexistent/secret', done.stdout)


spec = importlib.util.spec_from_file_location('probe', ROOT / 'tools/probe-api.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeResponse(io.BytesIO):
    def __init__(self, data, status):
        super().__init__(data)
        self.status = status


class ProbeTransportTests(unittest.TestCase):
    def test_binary_transport_identity_headers_and_prefix(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            return FakeResponse(b'{"next_offset":3}', 200)
        client = probe.ApiClient('https://example.invalid/prefix', opener)
        self.assertEqual({'next_offset': 3}, client.call('PUT', '/v1/artifacts/u/chunks/0', raw=b'\x00\xffx', token='fixture-token', identity=IDENTITY))
        self.assertEqual('https://example.invalid/prefix/v1/artifacts/u/chunks/0', seen[0].full_url)
        self.assertEqual(b'\x00\xffx', seen[0].data)
        headers = {k.lower(): v for k, v in seen[0].header_items()}
        self.assertEqual(IDENTITY['attempt_id'], headers['x-attempt-id'])
        self.assertNotIn('x-fleet-attempt-id', headers)

    def test_download_keeps_binary_exact(self):
        client = probe.ApiClient('https://example.invalid', lambda request, timeout: FakeResponse(b'\xff\x00', 200))
        self.assertEqual(b'\xff\x00', client.call('GET', '/fixture', binary=True))

    def test_http_error_expected_without_secret_in_error(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 409, 'fixture', {}, io.BytesIO(b'{"conflict":true}'))
        client = probe.ApiClient('https://example.invalid', opener)
        self.assertEqual({'conflict': True}, client.call('POST', '/fixture', token='private-test-value', status=409))
        with self.assertRaises(AssertionError) as raised:
            client.call('POST', '/fixture', token='private-test-value')
        self.assertNotIn('private-test-value', str(raised.exception))

    def test_raw_and_json_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            probe.ApiClient('https://example.invalid').call('PUT', '/fixture', value={}, raw=b'x')


if __name__ == '__main__':
    unittest.main()
