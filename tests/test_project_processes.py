import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import project_processes


class ProjectProcessesTests(unittest.TestCase):
    def test_only_exact_script_path_in_this_project_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            own = root / "config_web.py"
            other = root.parent / "other" / "config_web.py"
            output = json.dumps([
                {"ProcessId": 101, "CommandLine": f'python.exe -u "{own}"'},
                {"ProcessId": 102, "CommandLine": f'python.exe -u "{other}"'},
                {"ProcessId": 103, "CommandLine": f'python.exe -u "{own}.old"'},
            ])
            with patch.object(project_processes.os, "name", "nt"), patch.object(
                project_processes.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=output)
            ):
                self.assertEqual(project_processes.matching_pids(root, ("config_web.py",)), [101])

    def test_replaces_matching_process_before_new_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(project_processes, "matching_pids", return_value=[101]), patch.object(
                project_processes.subprocess, "run", return_value=SimpleNamespace(returncode=0)
            ) as command, patch.object(project_processes.time, "sleep"):
                stopped = project_processes.replace_existing(Path(directory), ("bot.py",))
            self.assertEqual(stopped, [101])
            self.assertEqual(command.call_args.args[0], ["taskkill", "/PID", "101", "/T", "/F"])

    def test_never_kills_current_virtualenv_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            own = root / "config_web.py"
            output = json.dumps([
                {"ProcessId": 500, "ParentProcessId": 101, "CommandLine": f'python.exe -u "{own}"'},
                {"ProcessId": 101, "ParentProcessId": 1, "CommandLine": f'python.exe -u "{own}"'},
                {"ProcessId": 102, "ParentProcessId": 1, "CommandLine": f'python.exe -u "{own}"'},
            ])
            with patch.object(project_processes.os, "name", "nt"), patch.object(
                project_processes.os, "getpid", return_value=500
            ), patch.object(project_processes.subprocess, "run",
                            return_value=SimpleNamespace(returncode=0, stdout=output)):
                self.assertEqual(project_processes.matching_pids(root, ("config_web.py",)), [102])


if __name__ == "__main__":
    unittest.main()
