# PR #7 retained validation evidence

See [validation](../../pr7-validation.md) for results and scope, and the
[difference audit](../../pr7-remaining-differences.md) for remaining exceptions.

- `build-manifest.json` is the frozen builder's unmodified manifest, including
  all three executable SHA-256 hashes and the source inventory.
- `test-logs.json` indexes the compressed test logs and hashes their original
  uncompressed bytes. Read a log with `gzip -dc <name>.log.gz`.
- `run-replay.sh` records the exact local replay invocation and retained input
  locations. It requires those artifacts and a fresh output directory; it is
  evidence of this run, not the deferred release distribution workflow.
- `replay.json` is the unmodified final replay manifest: 25 completed gates,
  all passed with unchanged identities. Each gate's output is retained as
  `<gate>.log.gz`.
- `post-replay-documentation.json` identifies the two contract prose corrections
  made after replay. `replayed-contracts/` preserves their original bytes so
  every replay input hash remains traceable.

Absolute paths identify this validation environment. Binary artifacts and full
per-case captures remain at the paths recorded in the manifests; executables
are not checked into the repository. Logs contain loopback fixture data only.
