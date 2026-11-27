#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -z "${BENCH_ROOT:-}" ]]; then
	for candidate in "$REPO_ROOT/../.." "$REPO_ROOT/../neuradix-bench" "$REPO_ROOT/../neuradix-cloud-bench"; do
		if [[ -d "$candidate/apps/frappe" && -x "$candidate/env/bin/python" ]]; then
			BENCH_ROOT="$(cd "$candidate" && pwd -P)"
			break
		fi
	done
fi
if [[ -z "${BENCH_ROOT:-}" || ! -d "$BENCH_ROOT/apps/frappe" ]]; then
	echo "Set BENCH_ROOT to a Frappe bench containing apps/frappe and env/bin/python." >&2
	exit 1
fi
PYTHON_BIN="${PYTHON_BIN:-$BENCH_ROOT/env/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
	echo "Python interpreter is not executable: $PYTHON_BIN" >&2
	exit 1
fi

export PYTHONPATH="$REPO_ROOT:$BENCH_ROOT/apps/frappe:$BENCH_ROOT/apps/erpnext:$BENCH_ROOT/apps/insight:$BENCH_ROOT/apps/neuradix:$BENCH_ROOT/apps/neuradix_cassarcamilleri${PYTHONPATH:+:$PYTHONPATH}"

exec "$PYTHON_BIN" -m unittest discover \
	-s "$REPO_ROOT/neuradix_cassar_ops/tests" \
	-t "$REPO_ROOT" "$@"

