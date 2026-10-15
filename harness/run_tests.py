#!/usr/bin/env python3
"""Run each harness test module in a fresh interpreter.

Some pure-mock suites install process-wide guards before importing production
modules. Discovery must therefore happen separately for each test file. Use
unittest's module loader rather than executing files as __main__, which would
change the production source-admission inventory.
"""

from pathlib import Path
import subprocess
import sys


TEST_DIR = Path(__file__).resolve().parent


def test_files(directory):
    return sorted(path for path in directory.glob('test_*.py') if path.is_file())


def run_tests(directory=TEST_DIR):
    files = test_files(directory)
    if not files:
        print(f'No harness test modules found in {directory}', file=sys.stderr)
        return 1
    failed = False
    for path in files:
        print(f'\n=== {path.name} ===', flush=True)
        result = subprocess.run(
            [sys.executable, '-m', 'unittest', 'discover', '-s', str(directory),
             '-p', path.name, '-v'],
            check=False,
        )
        failed = failed or result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(run_tests())
