# Standalone to avoid the root Makefile's Go/ldflags evaluation for every target.
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
GO ?= go
PYTHON ?= python3
CC ?= cc
CARGO ?= cargo
VERSION ?= v0.0.0-harness
REPETITIONS ?= 15
WARMUP ?= 5
OUT ?= $(ROOT)/harness/results/$(shell date -u +%Y%m%dT%H%M%SZ)

.PHONY: build tools baseline self-check test rust-build rust-test phase1-check phase1-measure phase2-build phase2-check phase2-measure baseline-cloud rust-cloud-build
.PHONY: phase2-workloads-measure
.PHONY: phase3-build phase3-reference-check phase3-check phase3-deadline-check phase3-measure phase3-workloads-measure

build: tools baseline

tools:
	mkdir -p $(ROOT)/bin
	$(CC) -O2 -Wall -Wextra -Werror -o $(ROOT)/bin/pb-meter $(ROOT)/harness/meter.c
	cd $(ROOT) && $(GO) build -trimpath -o bin/pb-fixture ./harness/fixture

baseline:
	mkdir -p $(ROOT)/bin
	cd $(ROOT) && CGO_ENABLED=0 $(GO) build -buildvcs=false -trimpath -tags kqueue -ldflags '-s -w -X main.Version=$(VERSION) -X main.Commit=harness' -o bin/pb-go .

self-check:
	$(PYTHON) $(ROOT)/harness/run.py run --binary go=$(ROOT)/bin/pb-go --binary identical=$(ROOT)/bin/pb-go --fixture $(ROOT)/bin/pb-fixture --out $(OUT) --repetitions $(REPETITIONS)

test:
	cd $(ROOT) && $(GO) test ./harness/fixture
	$(PYTHON) -m unittest discover -s $(ROOT)/harness -p 'test_*.py' -v

# Keep candidate binaries separate from the Go reference and installed CLI.
rust-build:
	cd $(ROOT) && PB_VERSION=$(VERSION) PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN= $(CARGO) build --locked --release

rust-test:
	cd $(ROOT) && $(CARGO) fmt --check
	cd $(ROOT) && $(CARGO) clippy --locked --all-targets -- -D warnings
	cd $(ROOT) && $(CARGO) test --locked

# OUT must be a new directory. phase1-check emits the measured case subset.
phase1-check:
	$(PYTHON) $(ROOT)/harness/phase1.py compare --reference $(ROOT)/bin/pb-go --candidate $(ROOT)/target/release/pb --out $(OUT)

phase1-measure:
	$(PYTHON) $(ROOT)/harness/run.py run --suite $(OUT)/benchmark-suite.json --binary go=$(ROOT)/bin/pb-go --binary rust=$(ROOT)/target/release/pb --out $(OUT)/performance --repetitions $(REPETITIONS) --warmup $(WARMUP)

# Deliberately public fixture credentials, never an account token. Separate
# artifacts let missing-release-configuration cases stay in the same gate.
baseline-cloud:
	mkdir -p $(ROOT)/bin
	cd $(ROOT) && CGO_ENABLED=0 $(GO) build -buildvcs=false -trimpath -tags kqueue -ldflags '-s -w -X main.Version=$(VERSION) -X main.Commit=harness -X github.com/parseablehq/pb/pkg/config.CloudOrchestratorAuthToken=harness-orchestrator-token' -o bin/pb-go-cloud .

rust-cloud-build:
	cd $(ROOT) && PB_VERSION=$(VERSION) PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN=harness-orchestrator-token $(CARGO) build --locked --release --target-dir target/harness-cloud

phase2-build: build rust-build baseline-cloud rust-cloud-build

phase2-check:
	$(PYTHON) $(ROOT)/harness/phase2.py compare --reference $(ROOT)/bin/pb-go --candidate $(ROOT)/target/release/pb --cloud-reference $(ROOT)/bin/pb-go-cloud --cloud-candidate $(ROOT)/target/harness-cloud/release/pb --fixture $(ROOT)/bin/pb-fixture --out $(OUT)

phase2-measure:
	$(PYTHON) $(ROOT)/harness/run.py run --suite $(OUT)/benchmark-suite.json --binary go=$(ROOT)/bin/pb-go --binary rust=$(ROOT)/target/release/pb --fixture $(ROOT)/bin/pb-fixture --out $(OUT)/performance --repetitions $(REPETITIONS) --warmup $(WARMUP)

phase2-workloads-measure:
	$(PYTHON) $(ROOT)/harness/phase2_workloads.py --out $(OUT)/workload-suite.json
	$(PYTHON) $(ROOT)/harness/run.py run --suite $(OUT)/workload-suite.json --binary go=$(ROOT)/bin/pb-go --binary rust=$(ROOT)/target/release/pb --fixture $(ROOT)/bin/pb-fixture --out $(OUT)/workloads --repetitions $(REPETITIONS) --warmup $(WARMUP)

phase3-build: phase2-build

.PHONY: phase4-build phase4-check phase4-deadline-check phase4-workloads-measure
phase4-build: phase3-build

phase4-check:
	$(PYTHON) $(ROOT)/harness/phase4.py compare --reference $(ROOT)/bin/pb-go --cloud-reference $(ROOT)/bin/pb-go-cloud --candidate $(ROOT)/target/release/pb --cloud-candidate $(ROOT)/target/harness-cloud/release/pb --out $(OUT)

# Freeze large-workload outputs before timing; pass REPETITIONS=30 WARMUP=5.
phase4-workloads-measure:
	mkdir -p $(OUT)
	$(PYTHON) $(ROOT)/harness/phase4_workloads.py --out $(OUT)/oracle-suite.json
	$(PYTHON) $(ROOT)/harness/run.py run --phase4-oracle-self-check --binary go=$(ROOT)/bin/pb-go --binary identical=$(ROOT)/bin/pb-go --fixture $(ROOT)/bin/pb-fixture --suite $(OUT)/oracle-suite.json --repetitions 1 --warmup 0 --timeout 120 --out $(OUT)/oracle
	$(PYTHON) $(ROOT)/harness/phase4_workloads.py --oracle-report $(OUT)/oracle/report.json --out $(OUT)/workload-suite.json
	$(PYTHON) $(ROOT)/harness/run.py run --binary go=$(ROOT)/bin/pb-go --binary rust=$(ROOT)/target/release/pb --fixture $(ROOT)/bin/pb-fixture --suite $(OUT)/workload-suite.json --repetitions $(REPETITIONS) --warmup $(WARMUP) --timeout 120 --out $(OUT)/workloads

# Full 120-second metrics deadlines require a separate long-running gate.
phase4-deadline-check:
	$(PYTHON) $(ROOT)/harness/phase4.py compare --reference $(ROOT)/bin/pb-go --candidate $(ROOT)/target/release/pb --include-full120s --case p4-deadline-header --case p4-deadline-body --out $(OUT)

# The default gate excludes the opt-in full60s and known-divergence tiers.
# Both Cloud artifacts are required; skips remain failures.
phase3-reference-check:
	$(PYTHON) $(ROOT)/harness/phase3.py check-reference --reference $(ROOT)/bin/pb-go --cloud-reference $(ROOT)/bin/pb-go-cloud --meter $(ROOT)/bin/pb-meter --out $(OUT)

phase3-check:
	$(PYTHON) $(ROOT)/harness/phase3.py compare --reference $(ROOT)/bin/pb-go --candidate $(ROOT)/target/release/pb --cloud-reference $(ROOT)/bin/pb-go-cloud --cloud-candidate $(ROOT)/target/harness-cloud/release/pb --meter $(ROOT)/bin/pb-meter --out $(OUT)

# Separate, explicitly opt-in real 60-second deadline cases (80 s case budget).
phase3-deadline-check:
	$(PYTHON) $(ROOT)/harness/phase3.py compare --reference $(ROOT)/bin/pb-go --candidate $(ROOT)/target/release/pb --cloud-reference $(ROOT)/bin/pb-go-cloud --cloud-candidate $(ROOT)/target/harness-cloud/release/pb --meter $(ROOT)/bin/pb-meter --include-full60s --group deadline --out $(OUT)

phase3-measure:
	$(PYTHON) $(ROOT)/harness/run.py run --suite $(OUT)/benchmark-suite.json --binary go=$(ROOT)/bin/pb-go --binary rust=$(ROOT)/target/release/pb --fixture $(ROOT)/bin/pb-fixture --meter $(ROOT)/bin/pb-meter --out $(OUT)/performance --repetitions $(REPETITIONS) --warmup $(WARMUP)

# Use a new OUT/SQL workload artifact path after the acceptance report exists.
phase3-workloads-measure:
	$(PYTHON) $(ROOT)/harness/phase3_workloads.py --out $(OUT)/sql-workload-suite.json
	$(PYTHON) $(ROOT)/harness/run.py run --suite $(OUT)/sql-workload-suite.json --binary go=$(ROOT)/bin/pb-go --binary rust=$(ROOT)/target/release/pb --fixture $(ROOT)/bin/pb-fixture --out $(OUT)/sql-workloads --repetitions $(REPETITIONS) --warmup $(WARMUP)

# Diagnostic binaries are deliberately separate from the release comparison.
.PHONY: diagnostics-build diagnostics-measure
diagnostics-build:
	cd $(ROOT) && $(PYTHON) harness/go_diagnostics.py --go $(GO) --binary bin/pb-go-diagnostics
	cd $(ROOT) && PB_VERSION=$(VERSION) PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN= $(CARGO) build --locked --release --features diagnostics --target-dir target/diagnostics

diagnostics-measure:
	cd $(ROOT) && $(PYTHON) harness/diagnose.py --out $(OUT) --repetitions $(REPETITIONS) --warmup $(WARMUP)

# Optimized native stacks, without manual diagnostics hooks or stripped symbols.
.PHONY: profiling-build profiling-capture
profiling-build:
	mkdir -p $(ROOT)/bin $(ROOT)/target/tmp
	cd $(ROOT) && TMPDIR=$(ROOT)/target/tmp GOTMPDIR=$(ROOT)/target/tmp CGO_ENABLED=0 $(GO) build -buildvcs=false -trimpath -tags kqueue -ldflags '-X main.Version=$(VERSION) -X main.Commit=harness' -o bin/pb-go-symbolized .
	cd $(ROOT) && TMPDIR=$(ROOT)/target/tmp PB_VERSION=$(VERSION) PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN= $(CARGO) build --locked --profile profiling --target-dir target/symbolized

profiling-capture:
	cd $(ROOT) && $(PYTHON) harness/profile_external.py --out $(OUT) --repetitions $(REPETITIONS)
