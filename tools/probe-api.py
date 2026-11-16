#!/usr/bin/env python3
"""Run on the dedicated host: public HTTPS/auth/idempotence/result smoke proof.

Creates a uniquely capability-scoped fixture; no TV or legacy jobs are consumed.
"""
import argparse
import hashlib
import urllib.parse
import json
from pathlib import Path
import urllib.request
import urllib.error
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / '.local/probe.local.json')
parser.add_argument('--check', action='store_true', help='Validate local settings without issuing API requests')
args = parser.parse_args()
try:
    settings = json.loads(args.config.read_text())
    base = settings['api_base'].rstrip('/')
    secret_file = Path(settings['admin_secret_file'])
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('invalid HTTPS base')
except (OSError, ValueError, KeyError, TypeError):
    parser.error('Valid private probe settings are required')
if args.check:
    print('Private probe configuration is valid.')
    raise SystemExit(0)
admin = json.loads(secret_file.read_text())['admin_token']


def api(method, path, value=None, token=None, status=200):
    data = json.dumps(value).encode() if value is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        response = e
    with response:
        raw = response.read()
        assert response.status == status, (path, response.status, status)
    return json.loads(raw) if raw else None


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
changed = dict(job, input={'text': 'different payload'})
api('POST', '/v1/admin/jobs', changed, admin, 409)
claim = {'worker_id': worker, 'capabilities': [capability]}
attempt = api('POST', '/v1/jobs/claim', claim, device)['job']
assert attempt['id'] == job_id
assert api('POST', '/v1/jobs/claim', claim, device)['job']['attempt_id'] == attempt['attempt_id']
complete = {k: attempt[k] for k in ('attempt_id', 'fencing_token')}
complete.update(worker_id=worker, status='succeeded',
                result={'sha256': hashlib.sha256(job['input']['text'].encode()).hexdigest()})
receipt = api('POST', '/v1/jobs/' + job_id + '/complete', complete, device)
assert receipt['committed'] is True
assert api('POST', '/v1/jobs/' + job_id + '/complete', complete, device) == receipt
saved = api('GET', '/v1/admin/jobs/' + job_id, token=admin)
assert saved['status'] == 'succeeded' and saved['result']['result'] == complete['result']
api('POST', '/v1/jobs/claim', claim, device, 204)
print(json.dumps({'passed': True, 'job_id': job_id, 'worker_id': worker,
                  'checks': ['public TLS', 'admin authentication', 'worker isolation',
                             'enqueue idempotence', 'payload conflict', 'repeat claim',
                             'durable result', 'completion receipt replay']}, indent=2))
