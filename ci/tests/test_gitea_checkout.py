"""Regression for public Gitea installations below a URL path prefix."""
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def checkout_program():
    workflow = Path(__file__).resolve().parents[2] / ".gitea/workflows/release.yml"
    lines = workflow.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if "python3 -B - <<'PY_CHECKOUT'" in line) + 1
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "PY_CHECKOUT")
    return compile("\n".join(line[10:] for line in lines[start:end]), str(workflow), "exec")


class GiteaCheckoutTests(unittest.TestCase):
    def environment(self, **updates):
        result = {"GITEA_WEB_URL":"https://gitea.example.invalid/git/", "GITEA_REPOSITORY":"team/component", "FLEET_RELEASE_TAG":"v0.2.1"}
        result.update(updates)
        return result

    def test_checkout_preserves_server_path_and_fetches_exact_tag(self):
        commands = []
        def invoked(args, **kwargs):
            commands.append(args)
            return SimpleNamespace(returncode=1 if args[1:3] == ["remote","get-url"] else 0)
        with patch.dict(os.environ, self.environment()), patch.object(subprocess,"run",side_effect=invoked):
            exec(checkout_program(), {})
        self.assertIn(["git","remote","add","origin","https://gitea.example.invalid/git/team/component.git"],commands)
        fetch = next(command for command in commands if "fetch" in command)
        self.assertEqual(fetch[-1],"refs/tags/v0.2.1:refs/tags/v0.2.1")
        self.assertIn("credential.helper=",fetch)

    def test_malformed_tag_is_rejected_before_any_git_command(self):
        with patch.dict(os.environ,self.environment(FLEET_RELEASE_TAG="v0.2.1; touch unexpected")), patch.object(subprocess,"run") as run:
            with self.assertRaises(SystemExit):
                exec(checkout_program(),{})
            run.assert_not_called()

    def test_credentials_and_invalid_repository_cannot_reach_git(self):
        for change in ({"GITEA_WEB_URL":"https://user:secret@gitea.example.invalid/git"},{"GITEA_REPOSITORY":"team/../other"}):
            with patch.dict(os.environ,self.environment(**change)), patch.object(subprocess,"run") as run:
                with self.assertRaises(SystemExit):
                    exec(checkout_program(),{})
                run.assert_not_called()
