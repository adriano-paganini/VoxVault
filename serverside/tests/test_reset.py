import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


RESET_SCRIPT = Path(__file__).resolve().parents[1] / "reset.sh"


class ResetTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="voxvault-reset-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.project = self.directory / "project with spaces"
        self.project.mkdir()
        self.script = self.project / "reset.sh"
        self.script.write_bytes(RESET_SCRIPT.read_bytes())
        self.script.chmod(0o755)
        self.log = self.directory / "docker.log"
        binary_directory = self.directory / "bin"
        binary_directory.mkdir()
        docker = binary_directory / "docker"
        docker.write_text(
            '#!/bin/sh\n'
            'printf "%s|%s\\n" "$PWD" "$*" >> "$RESET_TEST_LOG"\n'
            'if [ "$*" = "${RESET_TEST_FAILURE:-}" ]; then exit 1; fi\n'
            'if [ "$*" = "image inspect voxvault-server:latest" ] && '
            '[ "${RESET_TEST_MISSING_IMAGE:-0}" = 1 ]; then exit 1; fi\n'
            'exit 0\n'
        )
        docker.chmod(0o755)
        self.environment = {
            **os.environ,
            "PATH": f"{binary_directory}:{os.environ['PATH']}",
            "RESET_TEST_LOG": str(self.log),
        }

    def run_reset(self, *arguments, **environment):
        return subprocess.run(
            [str(self.script), *arguments], cwd=self.directory,
            env={**self.environment, **environment},
            text=True, capture_output=True, check=False,
        )

    def calls(self):
        return [line.split("|", 1)[1] for line in self.log.read_text().splitlines()]

    def test_reset_uses_project_directory_and_stops_before_deleting_key(self):
        result = self.run_reset()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reset complete", result.stdout)
        self.assertEqual(self.calls(), [
            "compose --profile reset config --quiet",
            "image inspect voxvault-server:latest",
            "compose down -v",
            "compose --profile reset run --rm --no-deps reset-keys",
        ])
        for line in self.log.read_text().splitlines():
            self.assertEqual(line.split("|", 1)[0], str(self.project))

    def test_missing_image_is_built_before_removing_data(self):
        result = self.run_reset(RESET_TEST_MISSING_IMAGE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertLess(calls.index("compose build voxvault"), calls.index("compose down -v"))

    def test_failed_build_preserves_data_and_key(self):
        result = self.run_reset(
            RESET_TEST_MISSING_IMAGE="1", RESET_TEST_FAILURE="compose build voxvault",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("compose down -v", self.calls())
        self.assertNotIn("reset complete", result.stdout)

    def test_failed_shutdown_does_not_delete_key(self):
        result = self.run_reset(RESET_TEST_FAILURE="compose down -v")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls()[-1], "compose down -v")
        self.assertNotIn("reset complete", result.stdout)

    def test_failed_key_deletion_does_not_report_success(self):
        result = self.run_reset(
            RESET_TEST_FAILURE="compose --profile reset run --rm --no-deps reset-keys",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("reset complete", result.stdout)

    def test_invalid_configuration_prevents_reset(self):
        result = self.run_reset(RESET_TEST_FAILURE="compose --profile reset config --quiet")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), ["compose --profile reset config --quiet"])

    def test_unrecognized_arguments_do_not_run_docker(self):
        result = self.run_reset("--unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
