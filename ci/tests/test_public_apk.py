import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

MODULE = Path(__file__).resolve().parents[1] / "fleetci.py"
spec = importlib.util.spec_from_file_location("fleetci_public_apk", MODULE)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class PublicApkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = self.root / "android/release-sources.example.json"
        self.template.parent.mkdir(parents=True)
        self.sources = [
            {"id":"gitea","type":"gitea","url":"https://gitea.invalid/api/v1/repos/team/app/releases/latest"},
            {"id":"gitflic","type":"channel","url":"https://gitflic.ru/project/team/app/blob/raw?file=stable.json&branch=release-channel"},
            {"id":"github","type":"manifest","url":"https://github.com/team/app/releases/latest/download/android-manifest.json"}]
        self.apk = self.root / "worker.apk"

    def package(self, sources=None, extra=None):
        self.template.write_text(json.dumps(self.sources))
        with zipfile.ZipFile(self.apk,"w") as out:
            out.writestr("assets/release-sources.json",json.dumps(sources or self.sources))
            for name,data in (extra or {}).items():out.writestr(name,data)

    def test_public_payload_is_independent_of_private_locator_environment(self):
        self.package()
        ci.verify_public_apk(self.root,self.apk,{"FLEET_RELEASE_SOURCES_JSON":json.dumps([{"url":"https://hidden.example.invalid"}])})

    def test_private_endpoints_fail_even_if_accidentally_added_to_template(self):
        self.sources[0]["url"]="https://hidden.example.invalid/git"
        self.package()
        with self.assertRaisesRegex(ci.Failure,"public_apk_private_source_endpoint"):
            ci.verify_public_apk(self.root,self.apk,{})

    def test_known_job_credential_or_private_key_material_is_not_publishable(self):
        token="fixture-token-"+("0"*24)
        self.package(extra={"classes.dex":b"fixture "+token.encode("utf-16-le")})
        with self.assertRaisesRegex(ci.Failure,"public_apk_known_credential"):
            ci.verify_public_apk(self.root,self.apk,{"GITFLIC_TOKEN":token})
        self.package(extra={"bad.key":b"-----BEGIN "+b"PRIVATE KEY-----\nfixture"})
        with self.assertRaisesRegex(ci.Failure,"public_apk_private_key_material"):
            ci.verify_public_apk(self.root,self.apk,{})

    def test_unknown_secret_fields_and_credential_queries_are_refused(self):
        self.sources[0]["authorization"]="fixture-only"
        self.package()
        with self.assertRaisesRegex(ci.Failure,"public_apk_unknown_source_field"):
            ci.verify_public_apk(self.root,self.apk,{})
        self.sources[0].pop("authorization")
        self.sources[2]["url"]+="?token=fixture-only"
        self.package()
        with self.assertRaisesRegex(ci.Failure,"public_apk_credential_query"):
            ci.verify_public_apk(self.root,self.apk,{})
