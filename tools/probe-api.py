#!/usr/bin/env python3
"""Isolated HTTPS/auth/durability proof; optional effect and binary-stream checks.

Creates a unique capability-scoped fixture. Never consumes TV or legacy jobs.
The binary fixture is deliberately finite; production bodies use streaming APIs.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ApiClient:
    def __init__(self, base, opener=urllib.request.urlopen):
        self.base, self.opener = base.rstrip('/'), opener

    def call(self, method, path, value=None, token=None, status=200, *,
             raw=None, identity=None, binary=False, profile=None):
        if raw is not None and value is not None:
            raise ValueError('Choose JSON or raw body')
        data = raw if raw is not None else (json.dumps(value).encode() if value is not None else None)
        headers = {'Content-Type': 'application/octet-stream' if raw is not None else 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if profile is not None:
            headers['X-Worker-Profile'] = base64.urlsafe_b64encode(json.dumps(profile,separators=(',', ':'),ensure_ascii=False).encode()).decode().rstrip('=')
        if identity:
            headers.update({'X-Worker-Id': identity['worker_id'],
                            'X-Attempt-Id': identity['attempt_id'],
                            'X-Fencing-Token': str(identity['fencing_token'])})
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            # Connection/read inactivity bound for these small probe RPCs, not a job deadline.
            response = self.opener(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            expected = (status,) if isinstance(status, int) else status
            if response.status not in expected:
                raise AssertionError((path, response.status, status))
        if binary:
            return body
        return json.loads(body) if body else None


def network_artifact_checks(api, job_id, identity, device, admin):
    """Exercise gateway persistence without contacting any source/vendor URL."""
    prefix = '/v1/jobs/' + job_id
    fixture = bytes(range(256)) * 3 + 'Отчёт\u00a0готов'.encode()
    digest = hashlib.sha256(fixture).hexdigest()
    create = dict(identity, key='probe/' + str(uuid.uuid4()),
                  media_type='application/octet-stream', name='proof.bin')
    upload = api('POST', prefix + '/artifacts', create, device, 201)['upload_id']
    assert api('POST', prefix + '/artifacts', create, device)['upload_id'] == upload
    upload_path = '/v1/artifacts/' + urllib.parse.quote(upload, safe='')
    split = 257
    first = api('PUT', upload_path + '/chunks/0', token=device, raw=fixture[:split], identity=identity)
    assert first['offset'] == 0 and first['next_offset'] == split
    assert first['sha256'] == hashlib.sha256(fixture[:split]).hexdigest()
    assert api('PUT', upload_path + '/chunks/0', token=device,
               raw=fixture[:split], identity=identity) == first
    api('PUT', upload_path + '/chunks/0', token=device, status=409,
        raw=b'conflicting bytes', identity=identity)
    state = api('GET', upload_path + '/status', token=device, identity=identity)
    assert state['offset'] == split and state['segments_count'] == 1
    assert 'segments' not in state, 'Status must remain bounded'
    last = api('PUT', upload_path + '/chunks/' + str(split), token=device,
               raw=fixture[split:], identity=identity)
    assert last['next_offset'] == len(fixture)
    finish = dict(identity, length=len(fixture), sha256=digest)
    artifact = api('POST', upload_path + '/finish', finish, device)
    assert artifact['length'] == len(fixture) and artifact['sha256'] == digest
    assert api('POST', upload_path + '/finish', finish, device) == artifact
    assert api('GET', '/v1/admin/artifacts/' + artifact['artifact_id'] + '/content',
               token=admin, binary=True) == fixture
    api('GET', '/v1/admin/artifacts/' + artifact['artifact_id'] + '/content', token=device, status=401)
    content_path = '/v1/artifacts/' + artifact['artifact_id'] + '/content'
    assert api('GET', content_path, token=device, identity=identity, binary=True) == fixture
    api('GET', content_path, token=device, status=401)
    api('GET', content_path, token=device, identity=dict(identity, attempt_id=str(uuid.uuid4())), status=401)

    effect = prefix + '/effects/probe-never'
    source_request = {'effect_id': 'probe-never', 'url': 'https://example.invalid/fixture',
                      'method': 'POST', 'headers': {'Accept': '*/*'}, 'body_text': 'fixture',
                      'replay': 'never', 'response': {'mode': 'artifact'}}
    execution_id = str(uuid.uuid4())
    begin = dict(identity, execution_id=execution_id, request=source_request)
    reservation = api('POST', effect + '/begin', begin, device)
    assert reservation['state'] == 'execute'
    assert api('POST', effect + '/begin', begin, device) == reservation
    different_live_call = dict(begin, execution_id=str(uuid.uuid4()))
    assert api('POST', effect + '/begin', different_live_call, device)['state'] == 'uncertain'
    changed_request = dict(source_request, body_text='different request')
    api('POST', effect + '/begin', dict(begin, request=changed_request), device, 409)
    # No vendor POST was made: the probe records only a synthetic known fixture response.
    response = {'status': 200, 'headers': {'content-type': ['application/octet-stream']},
                'url': source_request['url'], 'artifact': artifact}
    recorded = dict(identity, execution_id=execution_id,
                    request_sha256=reservation['request_sha256'], response=response,
                    artifact_ids=[artifact['artifact_id']])
    receipt = api('POST', effect + '/finish', recorded, device)
    assert receipt['recorded'] is True
    assert api('POST', effect + '/finish', recorded, device) == receipt
    replay = api('POST', effect + '/begin', different_live_call, device)
    assert replay['state'] == 'completed' and replay['response'] == response
    observed = api('GET', effect + '/status', token=device, identity=identity)
    assert observed['state'] == 'completed' and observed['artifact_ids'] == [artifact['artifact_id']]

    safe_effect = prefix + '/effects/probe-safe'
    safe_request = {'effect_id': 'probe-safe', 'url': 'https://example.invalid/fixture',
                    'method': 'GET', 'replay': 'safe', 'response': {'mode': 'artifact'}}
    first_call = dict(identity, execution_id=str(uuid.uuid4()), request=safe_request)
    old = api('POST', safe_effect + '/begin', first_call, device)
    fresh_call = dict(first_call, execution_id=str(uuid.uuid4()))
    fresh = api('POST', safe_effect + '/begin', fresh_call, device)
    assert old['state'] == fresh['state'] == 'execute'
    assert fresh['execution_id'] == fresh_call['execution_id']
    api('POST', safe_effect + '/finish', dict(recorded, execution_id=first_call['execution_id'],
        request_sha256=old['request_sha256']), device, (200, 409))
    current = api('GET', safe_effect + '/status', token=device, identity=identity)
    assert current['execution_id'] == fresh_call['execution_id']
    assert current['state'] != 'completed', 'A stale execution must not clobber current reservation'
    api('POST', safe_effect + '/finish', dict(recorded, execution_id=fresh_call['execution_id'],
        request_sha256=fresh['request_sha256']), device)
    return artifact['artifact_id'], ['chunk durable offset', 'chunk repeat/conflict',
        'sealed binary exact download', 'device scoped artifact recovery', 'bounded upload status', 'effect live nonce replay',
        'uncertain never effect', 'effect payload conflict', 'effect finish replay',
        'completed effect replay', 'safe takeover fencing']


def run_probe(base, admin, network_artifacts=False, opener=urllib.request.urlopen):
    api = ApiClient(base, opener).call
    run = uuid.uuid4().hex
    worker = 'api-proof-' + run
    api('GET', '/healthz')
    api('GET', '/v1/admin/jobs', status=401)
    device = api('POST', '/v1/admin/workers', {'worker_id': worker}, admin, 201)['token']
    capability = 'proof.' + run
    api('POST', '/v1/workers/heartbeat', {'worker_id': worker, 'version_code': 0,
        'version_name': 'HTTPS probe', 'capabilities': [capability], 'abis': [], 'state': 'test'}, device)
    api('POST', '/v1/workers/heartbeat', {'worker_id': 'another-worker'}, device, 401)
    job = {'idempotency_key': 'api-proof-' + run, 'operation': 'sha256',
           'input': {'text': 'Durable HTTPS result'}, 'required_capabilities': [capability]}
    job_id = api('POST', '/v1/admin/jobs', job, admin, 201)['job_id']
    assert api('POST', '/v1/admin/jobs', job, admin, 201)['job_id'] == job_id
    api('POST', '/v1/admin/jobs', dict(job, input={'text': 'different payload'}), admin, 409)
    claim = {'worker_id': worker, 'capabilities': [capability]}
    attempt = api('POST', '/v1/jobs/claim', claim, device)['job']
    assert attempt['id'] == job_id
    assert api('POST', '/v1/jobs/claim', claim, device)['job']['attempt_id'] == attempt['attempt_id']
    identity = {key: attempt[key] for key in ('attempt_id', 'fencing_token')}
    identity['worker_id'] = worker
    checks = ['public TLS', 'admin authentication', 'worker isolation', 'enqueue idempotence',
              'payload conflict', 'repeat claim', 'durable result', 'completion receipt replay']
    artifact_ids = []
    if network_artifacts:
        artifact_id, extra_checks = network_artifact_checks(api, job_id, identity, device, admin)
        artifact_ids.append(artifact_id)
        checks.extend(extra_checks)
    complete = dict(identity, status='succeeded',
                    result={'sha256': hashlib.sha256(job['input']['text'].encode()).hexdigest()})
    if artifact_ids:
        complete['artifact_ids'] = artifact_ids
    receipt = api('POST', '/v1/jobs/' + job_id + '/complete', complete, device)
    assert receipt['committed'] is True
    assert api('POST', '/v1/jobs/' + job_id + '/complete', complete, device) == receipt
    saved = api('GET', '/v1/admin/jobs/' + job_id, token=admin)
    assert saved['status'] == 'succeeded' and saved['result']['result'] == complete['result']
    api('POST', '/v1/jobs/claim', claim, device, 204)
    return {'passed': True, 'job_id': job_id, 'worker_id': worker, 'checks': checks}


def run_profile_probe(base, admin, opener=urllib.request.urlopen):
    """Two isolated actors; authenticated server IDs, admission and read isolation."""
    api = ApiClient(base, opener).call
    nonce = uuid.uuid4().hex
    caps = ['profile-proof.' + nonce, 'other-profile-proof.' + nonce]
    def profile(capability):
        return {'schema': 1, 'capabilities': [capability], 'resources': {
            'cpu_logical_cores': 4, 'cpu_available_cores': 1.5,
            'ram_total_bytes': 4096, 'ram_available_bytes': 1000,
            'disk_total_bytes': 8192, 'disk_available_bytes': 4000,
            'gpu_status': 'detected', 'gpus': [{'index': 0, 'compute_units': 8,
                'memory_total_bytes': 512, 'memory_available_bytes': 400,
                'unified_memory': True}]}}
    profiles = [profile(cap) for cap in caps]
    tokens = [api('POST', '/v1/admin/enrollments', {}, admin, 201)['enrollment_token'] for _ in caps]
    connected = [api('POST', '/v1/workers/connect', {}, token, profile=p) for token, p in zip(tokens, profiles)]
    workers = [row['worker_id'] for row in connected]
    assert workers[0] != workers[1]
    uuid.UUID(workers[0]); uuid.UUID(workers[1])
    for worker, token, p in zip(workers, tokens, profiles):
        assert api('POST', '/v1/workers/connect', {}, token, profile=p)['worker_id'] == worker
        me = api('GET', '/v1/workers/me', token=token)
        assert me['worker_id'] == worker and me['profile']['schema'] == 1
        assert me['profile']['resources']['cpu_available_cores'] == 1.5
        api('GET', '/v1/jobs/current', token=token, status=204)
        api('GET', '/v1/admin/workers', token=token, status=401)
    own, foreign = workers; device, other = tokens; p = profiles[0]
    hard = {'idempotency_key': 'profile-proof.' + nonce, 'operation': 'sha256',
            'input': {'text': 'resource admission'}, 'required_capabilities': [caps[0]],
            'resource_requirements': {'cpu_cores': 2, 'ram_bytes': 800,
                                      'disk_bytes': 50, 'gpu_count': 1, 'gpu_memory_bytes': 300}}
    job_id = api('POST', '/v1/admin/jobs', hard, admin, 201)['job_id']
    claim = {'worker_id': own, 'capabilities': [caps[0]]}
    api('POST', '/v1/jobs/claim', claim, device, 204)  # No fresh header cannot prove resources.
    api('POST', '/v1/jobs/claim', claim, device, 204, profile=p)  # CPU budget too small.
    p['resources']['cpu_available_cores'] = 3
    api('POST', '/v1/jobs/claim', claim, device, 204, profile=p)  # Shared RAM 800+300 >1000.
    p['resources']['ram_available_bytes'] = 2000
    attempt = api('POST', '/v1/jobs/claim', claim, device, profile=p)['job']
    assert attempt['id'] == job_id
    assert api('GET', '/v1/jobs/current', token=device)['job']['attempt_id'] == attempt['attempt_id']
    api('GET', '/v1/jobs/current?worker_id=' + own, token=other, status=204)
    api('POST', '/v1/jobs/claim', claim, other, 401, profile=profiles[1])
    api('POST', '/v1/jobs/claim', dict(claim, capabilities=['spoofed']), device, 400, profile=p)
    api('GET', '/v1/admin/jobs/' + job_id, token=other, status=401)
    identity = {key: attempt[key] for key in ('attempt_id', 'fencing_token')}; identity['worker_id'] = own
    aid, extra = network_artifact_checks(api, job_id, identity, device, admin)
    for path in ('/v1/artifacts/' + aid + '/status', '/v1/artifacts/' + aid + '/content',
                 '/v1/jobs/' + job_id + '/effects/probe-never/status'):
        api('GET', path, token=other, identity=identity, status=401)
    completion = dict(identity, status='succeeded', result={'profile_admission': True}, artifact_ids=[aid])
    assert api('POST', '/v1/jobs/' + job_id + '/complete', completion, device)['committed']
    api('GET', '/v1/jobs/current', token=device, status=204)
    for path in ('/v1/artifacts/' + aid + '/status', '/v1/artifacts/' + aid + '/content',
                 '/v1/jobs/' + job_id + '/effects/probe-never/status'):
        api('GET', path, token=device, identity=identity, status=(401, 409))
    old_read = dict(identity, execution_id=str(uuid.uuid4()), request={
        'effect_id': 'probe-never', 'url': 'https://example.invalid/fixture', 'method': 'POST',
        'headers': {'Accept': '*/*'}, 'body_text': 'fixture', 'replay': 'never', 'response': {'mode': 'artifact'}})
    api('POST', '/v1/jobs/' + job_id + '/effects/probe-never/begin', old_read, device, (401, 409))
    assert api('POST', '/v1/jobs/' + job_id + '/complete', completion, device)['committed']
    return {'passed': True, 'worker_ids': workers, 'job_id': job_id,
            'checks': ['server UUID', 'connect replay identity', 'own profile only', 'no global device view',
                       'fresh profile resource admission', 'fractional CPU', 'unified RAM accounting',
                       'own live assignment', 'foreign worker ID rejected', 'header/body consistency',
                       'foreign data rejected', 'completed data no longer readable', 'late receipt replay'] + extra}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / '.local/probe.local.json')
    parser.add_argument('--check', action='store_true', help='Validate settings without API requests')
    parser.add_argument('--profiles', action='store_true', help='Also verify server IDs, resource admission and current-only data access')
    parser.add_argument('--network-artifacts', action='store_true', help='Also verify effect journal and binary artifact extension')
    args = parser.parse_args()
    try:
        settings = json.loads(args.config.read_text())
        base = settings['api_base'].rstrip('/')
        secret_file = Path(settings['admin_secret_file'])
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Invalid HTTPS base')
    except (OSError, ValueError, KeyError, TypeError):
        parser.error('Valid private probe settings are required')
    if args.check:
        print('Private probe configuration is valid.')
        return
    admin = json.loads(secret_file.read_text())['admin_token']
    print(json.dumps(run_profile_probe(base, admin) if args.profiles else run_probe(base, admin, args.network_artifacts), indent=2))


if __name__ == '__main__':
    main()
