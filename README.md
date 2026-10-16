# pb — Parseable CLI

<p>
<a href="https://github.com/VibeEngineeringAI/pb-rs/actions/workflows/release.yaml"><img src="https://github.com/VibeEngineeringAI/pb-rs/actions/workflows/release.yaml/badge.svg?branch=main" alt="Build"></a>
<a href="https://www.rust-lang.org/"><img src="https://img.shields.io/badge/Rust-1.96-orange?logo=rust" alt="Rust 1.96"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg" alt="License"></a>
<a href="https://github.com/VibeEngineeringAI/pb-rs/releases"><img src="https://img.shields.io/github/v/release/VibeEngineeringAI/pb-rs?include_prereleases" alt="Latest release"></a>
</p>

![pb onboard](docs/images/pb-onboard.png)

Parseable in your terminal. `pb` lets you query logs with SQL, run PromQL against metrics streams, tail live events, and manage Parseable datasets, users, roles, and profiles without leaving your shell.

Query production logs. Explore metrics. Stream new events. Save repeatable investigations. Move between local, staging, and production Parseable instances with named profiles.

*"Don't guess. Query the logs."*

## What is pb?

`pb` is the command line interface for [Parseable](https://github.com/parseablehq/parseable). It gives operators and developers a fast terminal workflow for:

- SQL log queries with text or JSON output
- Interactive terminal table views for large result sets
- PromQL range and instant queries
- Metrics metadata exploration: labels, series, cardinality, and TSDB stats
- Live event tailing from Parseable datasets
- Dataset, user, role, and profile management
- Human-readable terminal output and structured JSON for automation and agents

For the Rust conversion, the [CLI comparison harness](harness/README.md) measures
prebuilt Go and Rust binaries against isolated HTTP/Arrow Flight fixtures. It
checks observable compatibility alongside startup, latency, throughput, CPU and
peak memory, and saves repeatable reports for regression tracking.

The Rust candidate implements the [offline CLI surface](docs/rust-phase1.md)
and [phase-two profiles, authentication, status and dataset reads](docs/rust-phase2.md),
plus [phase-three noninteractive SQL with streaming text/JSON output](docs/rust-phase3.md)
and [phase-four PromQL, metrics metadata, and saved SQL](docs/rust-phase4.md),
plus [phase-five dataset, user, and role administration](docs/rust-phase5.md).
This repository publishes the native Rust implementation. The retained Go sources
serve as the compatibility reference for the port.

The Rust candidate also adds [file collection and bounded session observation](docs/shoulder.md):
collect JSONL or text files on one machine, then let an external agent read and
acknowledge batches on another. Each observer keeps an independent cursor; the
agent supplies reasoning and delegation.

## Quick Start

```sh
# Connect to a Parseable server
pb login

# Open the interactive SQL table view
pb sql run -i

# Open SQL with a pre-filled query
pb sql run "SELECT * FROM backend" --from=1h -i

# Open the interactive PromQL table view
pb promql run -i

# Open PromQL with a pre-filled query
pb promql run "rate(http_requests_total[5m])" --dataset otel_metrics --from=1h -i

# Stream live events
pb tail backend
```

## Installation

The native Rust builds are distributed through
[GitHub Releases](https://github.com/VibeEngineeringAI/pb-rs/releases).
The [installation page](https://vibeengineeringai.github.io/pb-rs/) provides platform-specific commands
and copy buttons. The first Rust release is **v0.1.0-rc.1**, a preview release.
The commands below pin that prerelease; GitHub’s `latest` URL only includes stable releases.

**Linux / macOS:**

```sh
curl -fsSL https://github.com/VibeEngineeringAI/pb-rs/releases/download/v0.1.0-rc.1/install.sh | VERSION=0.1.0-rc.1 sh
```

The installer detects your architecture, verifies the archive's SHA-256 checksum,
and installs `pb` to `~/.local/bin`. Follow its printed instructions if that directory
is not on your `PATH`. No Rust toolchain is needed.

**Windows (PowerShell):**

```powershell
$env:VERSION = '0.1.0-rc.1'
try { irm https://github.com/VibeEngineeringAI/pb-rs/releases/download/v0.1.0-rc.1/install.ps1 | iex }
finally { Remove-Item Env:VERSION }
```

Installs `pb.exe` to `%LOCALAPPDATA%\Programs\pb\bin` and adds that directory to your user `PATH`.

**Choose a version or directory:**

Use the installer from the desired release to pin both the script and binary:

```sh
curl -fsSL https://github.com/VibeEngineeringAI/pb-rs/releases/download/v0.1.0-rc.1/install.sh | VERSION=0.1.0-rc.1 INSTALL_DIR="$HOME/.local/bin" sh
```

```powershell
$env:VERSION = '0.1.0-rc.1'
irm https://github.com/VibeEngineeringAI/pb-rs/releases/download/v0.1.0-rc.1/install.ps1 | iex
Remove-Item Env:VERSION
```

Replace `0.1.0-rc.1` with an available release version. `INSTALL_DIR` is also supported
as an environment variable on Windows.

**Manual installation:**

Download the archive for your system and the matching `pb_<version>_checksums.txt`
from the [releases page](https://github.com/VibeEngineeringAI/pb-rs/releases),
verify its SHA-256 hash, then extract `pb` (or `pb.exe`) into a directory on your `PATH`.
Unix archives use `.tar.gz`; Windows archives use `.zip`.

**Verify and connect:**

```sh
pb --version
pb --help
pb login
```

**Build from source:**

Install Rust 1.96 or later and the native C/C++ build tools required by the TLS and
compression dependencies, then run:

```sh
cargo build --locked --release
# Binary: target/release/pb (Windows: target/release/pb.exe)
```

The upstream `parseablehq/tap/pb` Homebrew formula distributes the upstream CLI;
use this repository's releases to install this Rust build.

## Authentication

The initial Rust prerelease supports self-hosted connections. Cloud device login
requires release-time orchestrator configuration; this repository currently has
no such configuration.

`pb` supports self-hosted Parseable and Parseable Cloud. Authentication details
are stored in a named local profile so subsequent commands can connect without
asking for credentials again.

**Interactive login wizard:**

```bash
pb login
```

Choose one of the following targets in the wizard:

- **Self-hosted:** enter the Parseable URL and authenticate with a username and
  password or an API key.
- **Parseable Cloud:** authenticate through the browser or with a Parseable
  Cloud API key. The selected workspace is saved as a local profile.

**Add a self-hosted profile without prompts:**

```bash
pb profile add local http://localhost:8000 admin admin
pb profile add local-key http://localhost:8000 --api-key psk_xxx
```

**Add a Parseable Cloud API-key profile without prompts:**

```bash
pb cloud profile add --api-key psk_xxx --name production
```

**Manage profiles:**

```bash
pb profile list
pb profile default prod
pb profile update prod https://new-parseable.example.com
pb profile remove prod
pb logout
```

Config file location:

| Platform | Path |
|---|---|
| macOS/Linux | `~/.config/pb/config.toml` |
| Windows | `%AppData%\pb\config.toml` |

**Verify connection:** `pb status`

## See It in Action

**Open the interactive SQL TUI:**

```sh
pb sql run -i
```

Start with a pre-filled query:

```sh
pb sql run "SELECT * FROM backend-shop WHERE order.amount > 999 LIMIT 5" --from=1h -i
```
![pb SQL interactive TUI](docs/images/pb-sql-tui.png)

**Run SQL without the TUI:**

```sh
pb sql run "SELECT * FROM backend WHERE status >= 500 LIMIT 5" --from=1h
```

**Open the interactive PromQL TUI:**

```sh
pb promql run -i
```

Start with a pre-filled query:

```sh
pb promql run "process.cpu.time{process.cpu.state!=""}" --dataset astronomy-shop-metrics --from=1h -i
```

![pb PromQL interactive TUI](docs/images/pb-promql-tui.png)

**Run PromQL without the TUI:**

```sh
pb promql run "sum(rate(http_requests_total[5m]))" --dataset otel_metrics --from=1h
```

**Stream live events:**

```sh
pb tail backend | jq 'select(.level == "error")'
```

`pb tail` uses gRPC. Make sure the server's gRPC port is reachable in addition to the main HTTP port.

## SQL Workflows

Interactive mode is the primary SQL workflow:

```bash
pb sql run -i
pb sql run "SELECT * FROM backend" --from=1h -i
```

Panels: Query, Time Range, Dataset, Columns, and Table. Navigate with Tab and Shift+Tab.

```bash
pb sql run "SELECT * FROM backend" --from=10m --to=now
pb sql run "SELECT * FROM backend" --from=1h --output json | jq .
pb sql run "SELECT * FROM backend WHERE status = 500" --from=1h --save-as=server-errors
pb sql save "SELECT * FROM backend WHERE status = 500" --name=server-errors
pb sql list
```

SQL queries without an explicit `LIMIT` return at most 500 rows. Add a SQL
`LIMIT` when you need more rows, including with `--output json`; JSON output
does not fetch additional pages. Interactive mode (`-i`) fetches 500-row
windows up to the SQL limit.

OTel fields with dots like `service.name` and `http.status_code` work directly in queries without manual quoting.

## PromQL Workflows

Interactive mode is the primary PromQL workflow:

```bash
pb promql run -i
pb promql run "http_requests_total" --dataset otel_metrics --from=1h -i
```

Panels: Dataset, Query, Time, Step, and Table. Press Space on the Step panel to toggle between range and instant mode.

```bash
pb promql run "rate(http_requests_total[5m])" --dataset otel_metrics --from=1h --step=1m
pb promql run "up" --dataset otel_metrics --instant
pb promql run "http_requests_total" --dataset otel_metrics --output json
```

Explore labels and series:

```bash
pb promql labels --dataset otel_metrics
pb promql label-values job --dataset otel_metrics
pb promql series --match 'http_requests_total{job="api"}' --dataset otel_metrics
```

Cardinality and TSDB analysis:

```bash
pb promql cardinality label-names --dataset otel_metrics
pb promql cardinality label-values --dataset otel_metrics --label job
pb promql cardinality active-series --dataset otel_metrics
pb promql tsdb --dataset otel_metrics
pb promql active-queries
```

## Manage Parseable

```bash
# Datasets
pb dataset list
pb dataset info <dataset>
pb dataset schema <dataset>
pb dataset add <dataset>
pb dataset remove <dataset>

# Users
pb user list
pb user add <user> --role <role>
pb user set-role <user> <role1>,<role2>
pb user remove <user>

# Roles
pb role list
pb role add <role>
pb role remove <role>

# Client version and server status
pb status
pb version
```

`pb version` prints the local client version on one line. Use `pb version -o json` for the same version as JSON. For server and connection details, use `pb status`.

User and role behavior:

- `pb user add` creates self-hosted users and requires at least one existing role. Parseable Cloud users must be invited from the Cloud dashboard instead.
- `pb user set-role` adds the requested roles without removing the user's existing role assignments.
- `pb role add` starts an interactive prompt that asks only for a privilege: `admin`, `editor`, `ingestor`, `reader`, or `writer`.
- `pb role remove` (or `pb role rm`) deletes an existing role. If the role name does not exist, the CLI shows the available role names and does not send a delete request.

Short aliases are available for common commands:

```bash
pb sql ls
pb dataset ls
pb dataset rm <dataset>
pb dataset stat <dataset>
pb profile ls
pb profile rm <profile>
pb profile set-url <profile> <url>
pb user ls
pb user rm <user>
pb role ls
pb role rm <role>
pb promql ps
```

## Command Groups

| Area | Commands | What you can do |
|---|---|---|
| Query logs | `pb sql` | Run SQL, save queries, open interactive result tables |
| Query metrics | `pb promql` | Run PromQL, inspect labels/series/cardinality |
| Stream events | `pb tail` | Watch new events from a dataset |
| Collect files (Rust) | `pb ingest files` | Upload appended JSONL/text files with durable checkpoints |
| Observe a session (Rust) | `pb observe` | Read bounded batches and acknowledge local receipt progress |
| Datasets | `pb dataset` | List, inspect, create, and remove datasets |
| Profiles | `pb login`, `pb cloud profile`, `pb profile`, `pb logout` | Manage self-hosted and Parseable Cloud connections |
| Access control | `pb user`, `pb role` | Manage users and roles |
| System | `pb status`, `pb version` | Check connectivity and versions |

## Automation

Commands print human-readable text by default. For scripts, CI, and agents,
start by discovering the read-only command catalog and machine-readable help:

```bash
pb agent -o json
pb -o json
pb help dataset list -o json
```

The agent catalog contains only read-only commands. It records command strings,
scope, profile requirements, and safety constraints. Server-side permissions
remain authoritative.

Commands that support `-o json` return structured output:

```bash
pb status -o json
pb profile list -o json
pb dataset list -o json
pb dataset schema <dataset> -o json
pb sql list -o json
pb sql run "SELECT count(*) FROM backend" --from=1h --output json
pb promql run "up" --dataset otel_metrics --instant --output json
```

Use `pb <command> --help` to check output support. For automation, omit `-i`
so SQL and PromQL commands print output instead of opening the terminal UI.
Empty collections are encoded as `[]`, not `null`.

Errors use a stable envelope such as:

```json
{"error":{"code":"NOT_FOUND","message":"profile not found","retryable":false}}
```

`pb status -o json` returns a non-zero exit status when the server is unhealthy.
`pb tail <dataset> -o json` is a long-running stream with one JSON value per
line; it requires the server's gRPC port to be reachable and can be stopped
with Ctrl+C.

> ⚠️ **Warning for agent access:** Create a dedicated read-only API key or role
> with the minimum permissions required for queries and metadata reads. Do not
> give an agent an administrator or shared human credential. Read-only access
> must be enforced by Parseable on the server.

## Documentation

| Topic | Description |
|---|---|
| [Parseable](https://github.com/parseablehq/parseable) | Parseable server repository |
| [Releases](https://github.com/VibeEngineeringAI/pb-rs/releases) | Download pre-built binaries |
| [File collection and session observation](docs/shoulder.md) | Collect on one machine and drive an external agent with bounded, acknowledged reads |
| `pb --help` | List command groups |
| `pb <command> --help` | Command-specific help |

## Contributing

See the [contributing guide](CONTRIBUTING.md).

## License

AGPL-3.0 — see [LICENSE](LICENSE).
