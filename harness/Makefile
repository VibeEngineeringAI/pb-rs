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

build: tools baseline

tools:
	mkdir -p $(ROOT)/bin
	$(CC) -O2 -Wall -Wextra -Werror -o $(ROOT)/bin/pb-meter $(ROOT)/harness/meter.c
	cd $(ROOT) && $(GO) build -trimpath -o bin/pb-fixture ./harness/fixture

baseline:
	mkdir -p $(ROOT)/bin
	cd $(ROOT) && CGO_ENABLED=0 $(GO) build -trimpath -tags kqueue -ldflags '-s -w -X main.Version=$(VERSION) -X main.Commit=harness' -o bin/pb-go .

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
	cd $(ROOT) && CGO_ENABLED=0 $(GO) build -trimpath -tags kqueue -ldflags '-s -w -X main.Version=$(VERSION) -X main.Commit=harness -X github.com/parseablehq/pb/pkg/config.CloudOrchestratorAuthToken=harness-orchestrator-token' -o bin/pb-go-cloud .

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
