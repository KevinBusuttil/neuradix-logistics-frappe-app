from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestRunnerTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.temporary = tempfile.TemporaryDirectory(prefix="operations runner ")
		self.addCleanup(self.temporary.cleanup)
		self.root = Path(self.temporary.name).resolve()
		self.environment = os.environ.copy()
		for key in ("BENCH_ROOT", "PYTHON_BIN", "PYTHONPATH"):
			self.environment.pop(key, None)

	def _copy_runner(self, repository: Path) -> Path:
		repository.mkdir(parents=True)
		runner = repository / "run_tests.sh"
		shutil.copyfile(Path(__file__).resolve().parents[2] / "run_tests.sh", runner)
		return runner

	def _interpreter(self, path: Path) -> Path:
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nprintf "%s\\n" "$PYTHONPATH"\n')
		path.chmod(0o755)
		return path

	def _bench(self, path: Path) -> Path:
		(path / "apps" / "frappe").mkdir(parents=True)
		self._interpreter(path / "env" / "bin" / "python")
		return path

	def _run(self, runner: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
		return subprocess.run(
			["bash", str(runner), *arguments],
			cwd=self.root,
			env=self.environment,
			capture_output=True,
			text=True,
			check=False,
		)

	def test_relocated_app_in_bench_discovers_all_tests_and_forwards_options(self) -> None:
		bench = self._bench(self.root / "custom bench")
		repository = bench / "apps" / "relocated operations"
		result = self._run(self._copy_runner(repository), "-v")
		self.assertEqual(result.returncode, 0, result.stderr)
		lines = result.stdout.splitlines()
		self.assertEqual(lines[:8], [
			"-m", "unittest", "discover", "-s",
			str(repository / "neuradix_cassar_ops" / "tests"), "-t", str(repository), "-v",
		])
		self.assertEqual(lines[8].split(":"), [
			str(repository),
			*(str(bench / "apps" / name) for name in (
				"frappe", "erpnext", "insight", "neuradix", "neuradix_cassarcamilleri"
			)),
		])

	def test_sibling_bench_layouts_are_discovered(self) -> None:
		for name in ("neuradix-bench", "neuradix-cloud-bench"):
			with self.subTest(name=name):
				workspace = self.root / name
				bench = self._bench(workspace / name)
				result = self._run(self._copy_runner(workspace / "source"))
				self.assertEqual(result.returncode, 0, result.stderr)
				self.assertIn(str(bench / "apps" / "frappe"), result.stdout)

	def test_explicit_bench_and_interpreter_overrides_preserve_pythonpath(self) -> None:
		bench = self._bench(self.root / "explicit bench")
		interpreter = self._interpreter(self.root / "other python")
		self.environment.update(BENCH_ROOT=str(bench), PYTHON_BIN=str(interpreter), PYTHONPATH="extra-path")
		result = self._run(self._copy_runner(self.root / "source"))
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertTrue(result.stdout.rstrip().endswith(":extra-path"))

	def test_missing_bench_has_actionable_error(self) -> None:
		result = self._run(self._copy_runner(self.root / "source"))
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("Set BENCH_ROOT", result.stderr)

	def test_invalid_explicit_bench_is_rejected(self) -> None:
		self.environment["BENCH_ROOT"] = str(self.root / "missing bench")
		result = self._run(self._copy_runner(self.root / "source"))
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("Set BENCH_ROOT", result.stderr)

	def test_nonexecutable_interpreter_is_rejected(self) -> None:
		bench = self._bench(self.root / "bench")
		self.environment.update(BENCH_ROOT=str(bench), PYTHON_BIN=str(self.root / "missing python"))
		result = self._run(self._copy_runner(self.root / "source"))
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("Python interpreter is not executable", result.stderr)
