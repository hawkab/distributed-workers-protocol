"""Exercise the canonical signing command and resulting release-file census."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('signer_ci', Path(__file__).resolve().parents[1] / 'fleetci.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class AndroidSidecarTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'gradlew').touch()
        ci.save(self.root / 'ci/version.json', ci.json_bytes({'name': '0.3.2', 'code': 8}))
        self.unsigned = self.root / 'dist/unsigned/worker-unsigned.apk'
        ci.save(self.unsigned, b'actual unsigned build fixture')
        ci.save(self.root / 'dist/unsigned/build.json', ci.json_bytes({'tag': 'v0.3.2', 'commit': 'a' * 40,
            'version': {'name': '0.3.2', 'code': 8}, 'sha256': ci.sha(self.unsigned.read_bytes())}))
        self.signing_commands = []
        self.badging = b"package: name='local.hwak.fleetworker' versionCode='8' versionName='0.3.2'"

    def sdk_command(self, arguments, **kwargs):
        arguments = [str(x) for x in arguments]
        if arguments[0] == 'git':
            return b'1234567890'
        if arguments[0] == 'fixture-aapt':
            return self.badging
        if arguments[0] == 'fixture-apksigner':
            self.signing_commands.append(arguments)
            target = Path(arguments[arguments.index('--out') + 1])
            ci.save(target, b'signed APK fixture')
            if '--v4-signing-enabled' not in arguments or arguments[arguments.index('--v4-signing-enabled') + 1] != 'false':
                ci.save(Path(str(target) + '.idsig'), b'unsupported V4 sidecar')
            return b''
        if arguments[0] == 'openssl':
            return b'manifest signature fixture'
        raise AssertionError('unexpected command: ' + arguments[0])

    def test_canonical_signer_disables_v4_and_record_contains_no_sidecar(self):
        config = {'component': 'worker-android', 'android_certificate_sha256': 'b' * 64}
        with patch.object(ci, 'run', self.sdk_command), patch.object(ci, 'apk_tools', return_value=('fixture-apksigner', 'fixture-aapt')), \
             patch.object(ci, 'verify_public_apk') as public_check, patch.object(ci, 'verify_android') as signature_check, \
             patch.dict(ci.os.environ, {'FLEET_PUBLIC_RELEASE_BASE': 'https://github.com/hawkab/distributed-workers-android/releases/download/{tag}'}):
            out = self.root / 'dist/v0.3.2'
            record = ci.build(self.root, out, config, 'v0.3.2', 'a' * 40)
        names = {item['name'] for item in record['files']}
        self.assertEqual(names, {ci.ANDROID_APK, ci.ANDROID_MANIFEST})
        self.assertFalse(list(out.glob('*.idsig')))
        self.assertEqual(self.signing_commands[0][self.signing_commands[0].index('--v4-signing-enabled') + 1], 'false')
        self.assertIn('fleet-worker', self.signing_commands[0])
        public_check.assert_called_once_with(self.root, self.unsigned)
        signature_check.assert_called_once_with(self.root, out, config)
        ci.verify_record(out, 'worker-android', 'v0.3.2', 'a' * 40)

    def test_unsigned_package_identity_is_still_checked_before_signing(self):
        self.badging = b"package: name='foreign.package' versionCode='8' versionName='0.3.2'"
        with patch.object(ci, 'run', self.sdk_command), patch.object(ci, 'apk_tools', return_value=('fixture-apksigner', 'fixture-aapt')), \
             self.assertRaisesRegex(ci.Failure, 'unsigned_apk_identity_mismatch'):
            ci.build(self.root, self.root / 'dist/v0.3.2', {'component': 'worker-android'}, 'v0.3.2', 'a' * 40)
        self.assertEqual(self.signing_commands, [])
