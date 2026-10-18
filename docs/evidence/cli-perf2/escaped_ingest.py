"""Run the ingestion harness with fixed escape-heavy JSONL/text lines.

Use the same `run` arguments as harness/ingest_perf.py. This changes only corpus
construction; native measurement, wire/checkpoint comparisons and probes remain.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'harness'))
import ingest_perf


def jsonl_line(index, size):
    prefix = json.dumps({'i': index}, separators=(',', ':'))[:-1] + ',"msg":"'
    # Each escaped quote takes two source bytes. Fill the final odd byte safely.
    padding = size - len(prefix.encode()) - 3
    if padding < 0:
        raise ValueError('record size too small')
    line = (prefix + '\\"' * (padding // 2) + 'x' * (padding % 2) + '"}\n').encode()
    assert len(line) == size
    return line


def text_line(index, size):
    prefix = f'{index:08d} '.encode()
    if size <= len(prefix):
        raise ValueError('record size too small')
    return prefix + b'\t' * (size - len(prefix) - 1) + b'\n'


ingest_perf.jsonl_line = jsonl_line
ingest_perf.text_line = text_line
# build_corpus bounds input length using its ordinary padding alphabet. These
# generators do not use that alphabet; extend its capacity for this corpus while
# retaining a finite 64 KiB guard. Measurement and comparison code is unchanged.
ingest_perf.ALPHABET = ingest_perf.ALPHABET.ljust(65536 + 26, 'x')
if __name__ == '__main__':
    raise SystemExit(ingest_perf.main())
