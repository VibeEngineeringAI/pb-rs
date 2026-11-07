#!/usr/bin/env python3
"""Successful raw-text diagnostic using a prepared immutable stress response.

Prepare is deliberately separate from run: the existing Go stress fixture makes
the body once, outside every CLI measurement. Each replay process verifies its
SHA-256 before reporting ready and serves it with sendfile (bounded fallback).
This removes per-row producer work; it is not the primary generated benchmark.

Limitations recorded in every report: the replay answers HTTP/1.0 with
Content-Length and closes, whereas the Go stress fixture streams HTTP/1.1
chunked responses, so the CLI's chunk-decoding path is not exercised. The raw
calibration drain is a Python urllib reader that hashes every 64 KiB chunk, so
its "ceiling" is likely reader-bound; possibly_producer_bound can fire when the
producer is not the limit, and performance admission is then withheld.
"""
import argparse
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import random
import select
import stat
import statistics
import sys
import time
import urllib.request

import run as harness
import stress
from stress_process import ArrayObjectScanner


SCHEMA = 'pb-perf3-cached-body/1'
CHUNK = 1 << 20
MAX_REQUEST = 1 << 20
# Bounded so the pre-ready hash fits the stress fixture's 10 s readiness
# deadline on a warm page cache; a cold cache can still miss it.
MAX_BODY = 2 << 30
SEND_TIMEOUT = 30
SCOPE = ('cached-file raw-text diagnostic; body prepared by original stress fixture; '
         'no timed per-row generation, serialization or source hashing; HTTP/1.0 Content-Length '
         'framing (not the primary fixture\'s HTTP/1.1 chunked framing, so chunk decoding is not '
         'exercised); not a primary benchmark')
WIRE_FRAMING = {'cached': 'HTTP/1.0 Content-Length, connection close',
                'primary_fixture': 'HTTP/1.1 chunked',
                'scope': 'chunk-decoding path not exercised; do not extrapolate to the primary benchmark'}
CALIBRATION_READER = ('python urllib 64 KiB reads with per-chunk SHA-256 (stress.calibrate); likely '
                      'reader-bound, so possibly_producer_bound is conservative and may withhold '
                      'admission when the producer is not the limit')
HASH_BASIS = ('manifest sha256/rows verified by full re-hash before ready; fstat fingerprint unchanged '
              'before and after transfer; not computed from bytes sent')


def body_bound(config):
    """Conservative upper bound: '[' ']' plus each row's max length and comma."""
    return 2 + config['rows'] * (max(config.get('row_bytes', 20), 32) + 1)


def source_hashes():
    here = Path(__file__).resolve()
    return {str(p): stress.identity(p)['sha256'] for p in
            (here, here.with_name('stress.py'), here.with_name('stress_process.py'), here.with_name('run.py'))}


def interpreter():
    return {'executable': sys.executable, 'version': sys.version}


def read_json(path):
    with Path(path).open('rb') as source:
        raw = source.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        raise ValueError('configuration/manifest exceeds 1 MiB')
    return json.loads(raw)


def validate_config(config):
    allowed = {'rows', 'row_bytes', 'shape', 'chunk_bytes', 'chunk_delay_ms',
               'gzip', 'failure', 'fail_after_rows', 'fragment_pattern', 'flush'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('unsupported cached fixture configuration keys')
    if type(config.get('rows')) is not int or not 0 <= config['rows'] <= 2**63 - 1:
        raise ValueError('rows must be a nonnegative int64')
    if type(config.get('row_bytes', 20)) is not int or not 20 <= config.get('row_bytes', 20) <= 16 << 20:
        raise ValueError('row_bytes must be 20..16777216')
    if config.get('shape', 'tiny') not in ('tiny', 'ascii', 'mixed', 'escaped'):
        raise ValueError('unsupported shape')
    if body_bound(config) > MAX_BODY:
        raise ValueError(f'rows*row_bytes bound exceeds cached body cap of {MAX_BODY} bytes')
    # The replay has its own bounded transfer size; transport knobs are not
    # silently interpreted as fragmentation, gzip or failure injection.
    defaults = {'chunk_bytes': 65536, 'chunk_delay_ms': 0, 'gzip': False,
                'failure': 'none', 'fail_after_rows': 0, 'fragment_pattern': [], 'flush': False}
    for key, default in defaults.items():
        if key in config and (type(config[key]) is not type(default) or config[key] != default):
            raise ValueError(f'cached replay does not support {key}={config[key]!r}')
    return config


def query_body(rows):
    query = stress.QUERY + f' LIMIT {max(1, rows)}'
    return json.dumps({'query': query, 'startTime': '2024-01-01T00:00:00Z',
                       'endTime': '2024-01-01T01:00:00Z'}, separators=(',', ':')).encode()


def settled_stats(fixture):
    deadline = time.monotonic() + 2
    result = fixture.stats()
    while result.get('active_queries', 0) and time.monotonic() < deadline:
        time.sleep(.01)
        result = fixture.stats()
    return result


def prepare(args):
    config = validate_config(read_json(args.config))
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture_id = stress.identity(args.fixture)
    body = out / 'body.json'
    bound = min(body_bound(config), MAX_BODY)
    sources = source_hashes()
    created = False
    fixture = stress.Fixture(Path(fixture_id['path']), config, out / 'generation')
    try:
        request = urllib.request.Request(fixture.url + '/api/v1/query', data=query_body(config['rows']),
            headers={'Authorization': stress.AUTH, 'Content-Type': 'application/json'})
        digest, size, scanner = hashlib.sha256(), 0, ArrayObjectScanner()
        start = time.monotonic()
        with urllib.request.urlopen(request, timeout=args.timeout) as response, body.open('xb') as target:
            created = True
            while True:
                if time.monotonic() - start > args.timeout:
                    raise TimeoutError('cached body preparation exceeded deadline')
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                if size + len(chunk) > bound:
                    raise ValueError(f'generated response exceeds conservative bound of {bound} bytes')
                target.write(chunk)
                digest.update(chunk)
                scanner.feed(chunk, 0)
                size += len(chunk)
            target.flush()
            os.fsync(target.fileno())
        source_stats = settled_stats(fixture)
        rows = scanner.result()
        if not (rows['complete'] and rows['completed_rows'] == config['rows']
                and source_stats.get('query_requests') == 1 and source_stats.get('completed')
                and source_stats.get('rows_written') == config['rows']
                and source_stats.get('source_bytes') == size
                and source_stats.get('response_sha256') == digest.hexdigest()):
            raise ValueError('generated response failed exact source hash/size/row admission')
    except BaseException:
        # Only the partial body this call created; the out directory is new.
        if created:
            body.unlink(missing_ok=True)
        raise
    finally:
        fixture.close()
    body.chmod(0o444)
    manifest = {'schema': SCHEMA, 'scope': SCOPE, 'fixture_config': config,
                'body': {'path': str(body), 'sha256': digest.hexdigest(), 'size_bytes': size,
                         'rows': config['rows'], 'read_only': True},
                'provenance': {'fixture': fixture_id, 'fixture_stats': source_stats,
                               'framing_validation': rows,
                               'request_body_sha256': hashlib.sha256(query_body(config['rows'])).hexdigest(),
                               'size_bound_bytes': bound, 'harness_sources': sources,
                               'python': interpreter(),
                               'prepared_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}}
    path = out / 'manifest.json'
    harness.write_json(path, manifest)
    path.chmod(0o444)
    print(path, flush=True)
    return 0


def fingerprint(source):
    s = os.fstat(source.fileno())
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def load_body(path):
    """Validate provenance and exact body identity before any timed request."""
    manifest = read_json(path)
    if manifest.get('schema') != SCHEMA:
        raise ValueError('unsupported cached manifest schema')
    config = validate_config(manifest['fixture_config'])
    body, provenance = manifest['body'], manifest['provenance']
    source_stats, framed = provenance['fixture_stats'], provenance['framing_validation']
    fixture_id = provenance['fixture']
    digest = body['sha256']
    if not (isinstance(digest, str) and len(digest) == 64
            and all(c in '0123456789abcdef' for c in digest)
            and isinstance(fixture_id.get('sha256'), str) and len(fixture_id['sha256']) == 64
            and fixture_id.get('path') and fixture_id.get('size_bytes', 0) > 0
            and body.get('rows') == config['rows'] and body.get('read_only') is True
            and source_stats.get('query_requests') == 1 and source_stats.get('completed') is True
            and source_stats.get('rows_written') == config['rows']
            and source_stats.get('response_sha256') == digest
            and source_stats.get('source_bytes') == body['size_bytes']
            and type(body.get('size_bytes')) is int and 0 <= body['size_bytes'] <= MAX_BODY
            and framed.get('complete') is True and framed.get('completed_rows') == config['rows']
            and provenance.get('request_body_sha256') == hashlib.sha256(query_body(config['rows'])).hexdigest()):
        raise ValueError('cached body provenance/hash/row count mismatch')
    source = Path(body['path']).open('rb')
    try:
        mode = os.fstat(source.fileno()).st_mode
        before = fingerprint(source)
        if not stat.S_ISREG(mode) or mode & 0o222:
            raise ValueError('cached body must be a read-only regular file')
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if before != fingerprint(source) or before[2] != body['size_bytes'] or actual != digest:
            raise ValueError('cached body changed or hash/size mismatch')
        source.seek(0)
        return manifest, source, before
    except BaseException:
        source.close()
        raise


def wait_writable(connection):
    """Bounded wait after EAGAIN: a timeout socket is non-blocking at the OS level."""
    timeout = connection.gettimeout()
    timeout = SEND_TIMEOUT if timeout is None else timeout
    poller = select.poll()
    poller.register(connection.fileno(), select.POLLOUT)
    if not poller.poll(timeout * 1000):
        raise TimeoutError(f'cached sendfile: socket not writable within {timeout} seconds')


def transfer(connection, source, size):
    """Bounded file-to-socket transfer; a partial sendfile fallback keeps offset."""
    offset, writes, waits = 0, 0, 0
    use_sendfile = hasattr(os, 'sendfile')
    used_sendfile = False
    used_fallback = False
    while offset < size:
        if use_sendfile:
            try:
                count = os.sendfile(connection.fileno(), source.fileno(), offset, min(CHUNK, size - offset))
                used_sendfile = True
            except BlockingIOError:
                wait_writable(connection)
                waits += 1
                continue
            except OSError as error:
                if error.errno not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                    raise
                use_sendfile = False
                continue
            if not count:
                raise OSError('unexpected EOF in cached sendfile')
        else:
            used_fallback = True
            block = os.pread(source.fileno(), min(CHUNK, size - offset), offset)
            if not block:
                raise OSError('unexpected EOF in cached file')
            connection.sendall(block)
            count = len(block)
        offset += count
        writes += 1
    method = ('sendfile_then_bounded_pread_sendall' if used_sendfile and used_fallback else
              'sendfile' if used_sendfile else 'bounded_pread_sendall')
    return {'bytes': offset, 'writes': writes, 'writable_waits': waits, 'transfer': method}


class CachedServer(HTTPServer):
    def __init__(self, manifest, source, original_fingerprint):
        super().__init__(('127.0.0.1', 0), CachedHandler)
        self.manifest, self.source, self.original_fingerprint = manifest, source, original_fingerprint
        self.stats = {'query_requests': 0, 'active_queries': 0, 'completed': False,
                      'rows_written': 0, 'source_bytes': 0, 'response_sha256': None,
                      'response_sha256_basis': HASH_BASIS, 'rows_written_basis': HASH_BASIS,
                      'cached_producer': {'scope': SCOPE, 'body': manifest['body'],
                                          'original_fixture': manifest['provenance']['fixture'],
                                          'wire_framing': WIRE_FRAMING}}


class CachedHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, content_type='text/plain'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split('?', 1)[0] == '/__stats':
            self.reply(200, json.dumps(self.server.stats).encode(), 'application/json')
        elif self.path == '/api/v1/query':
            self.reply(405, b'cached fixture: method not allowed\n')
        else:
            self.reply(404, b'cached fixture: not found\n')

    def do_POST(self):
        if self.path != '/api/v1/query':
            self.reply(404, b'cached fixture: not found\n')
            return
        stats = self.server.stats
        stats['query_requests'] += 1
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_REQUEST or self.headers.get('Transfer-Encoding'):
            self.reply(413, b'cached fixture: bounded Content-Length required\n')
            return
        self.connection.settimeout(SEND_TIMEOUT)
        raw = self.rfile.read(length)
        authenticated = self.headers.get('Authorization') == stress.AUTH
        stats['last_query'] = {'body_sha256': hashlib.sha256(raw).hexdigest(), 'body_bytes': len(raw),
                               'method': 'POST', 'path': self.path, 'auth': 'ok' if authenticated else 'bad'}
        if len(raw) != length:
            self.reply(400, b'cached fixture: incomplete request\n')
            return
        if not authenticated:
            self.reply(401, b'cached fixture: unauthorized\n')
            return
        if fingerprint(self.server.source) != self.server.original_fingerprint:
            self.reply(500, b'cached fixture: body changed after readiness\n')
            return
        body = self.server.manifest['body']
        stats.update(active_queries=1, completed=False, rows_written=0, source_bytes=0, response_sha256=None)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(body['size_bytes']))
            self.end_headers()
            self.wfile.flush()
            sent = transfer(self.connection, self.server.source, body['size_bytes'])
            if fingerprint(self.server.source) != self.server.original_fingerprint:
                raise ValueError('cached body changed during transfer')
            stats.update(completed=True, rows_written=body['rows'], source_bytes=sent['bytes'],
                         response_sha256=body['sha256'], wire_bytes=sent['bytes'],
                         wire_writes=sent['writes'], writable_waits=sent['writable_waits'],
                         transfer=sent['transfer'])
        except (OSError, ValueError) as error:
            stats['transfer_error'] = str(error)
            self.close_connection = True
        finally:
            stats['active_queries'] = 0


def serve(config_path):
    config = read_json(config_path)
    if 'cached_manifest' not in config:
        raise ValueError('cached_manifest is required; use prepare first')
    manifest, source, original = load_body(config.pop('cached_manifest'))
    try:
        if config != manifest['fixture_config']:
            raise ValueError('replay configuration differs from prepared fixture configuration')
        with CachedServer(manifest, source, original) as server:
            print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid(),
                              'producer': 'cached_file', 'body_sha256': manifest['body']['sha256'],
                              'python': interpreter()}), flush=True)
            server.serve_forever()
    finally:
        source.close()


def paired_summary(samples):
    paired = {}
    for sample in samples:
        if not sample['warmup']:
            paired.setdefault(sample['round'], {})[sample['label']] = sample
    ratios = []
    for round_index, pair in sorted(paired.items()):
        if set(pair) != {'baseline', 'candidate'}:
            raise ValueError('incomplete baseline/candidate round')
        ratio = {'round': round_index}
        for key in ('wall_ms', 'cpu_ms', 'peak_rss_bytes'):
            values = [pair[label]['native_metrics']['peak_rss_bytes'] if key == 'peak_rss_bytes'
                      else pair[label]['rates'][key] for label in ('baseline', 'candidate')]
            ratio[key] = values[1] / values[0] if values[0] > 0 else None
        ratios.append(ratio)
    return {'scope': 'candidate/baseline ratios within randomized serial paired rounds; lower is better',
            'rounds': ratios, 'median_ratios': {key: statistics.median(
                [r[key] for r in ratios if r[key] is not None]) if any(r[key] is not None for r in ratios) else None
                for key in ('wall_ms', 'cpu_ms', 'peak_rss_bytes')}}


def run(args):
    manifest, source, _ = load_body(args.manifest)
    source.close()
    manifest_identity = stress.identity(args.manifest)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binary_ids = {label: stress.identity(path) for label, path in
                  (('go', args.go), ('baseline', args.reference), ('candidate', args.candidate))}
    fixture = Path(__file__).resolve()
    meter = Path(args.meter).resolve()
    spec = stress.case('cached-rawtext', output='text', scan_json=False)
    spec['fixture'] = {**manifest['fixture_config'], 'cached_manifest': str(args.manifest.resolve())}
    report = {'schema': 'pb-perf3-cached-run/1', 'scope': SCOPE, 'complete': False, 'passed': False,
              'binaries': binary_ids, 'meter': stress.identity(meter), 'fixture': stress.identity(fixture),
              'manifest': manifest, 'manifest_identity': manifest_identity,
              'cases': [spec], 'samples': [], 'errors': [], 'output_validations': {},
              'method': {'repetitions': args.repetitions, 'warmup': args.warmup, 'seed': args.seed,
                         'sample_interval': args.sample_interval, 'timeout': args.timeout,
                         'affinity': sorted(os.sched_getaffinity(0)), 'stdout_sink': 'null',
                         'go_role': 'exact full-output parity oracle; native performance compares Rust revisions',
                         'wire_framing': WIRE_FRAMING, 'calibration_reader': CALIBRATION_READER,
                         'fixture_response_sha256_basis': HASH_BASIS},
              'host': {'uname': list(os.uname()), 'cpu_count': os.cpu_count(), 'python': interpreter()},
              'sources': source_hashes()}
    try:
        calibration = stress.calibrate(fixture, spec, out / 'calibration', args.timeout)
        report['calibration'] = calibration
        if not calibration['valid'] or calibration['sha256'] != manifest['body']['sha256']:
            raise ValueError('cached independent raw-drain calibration failed')
        validation = []
        for label, binary in binary_ids.items():
            sample = stress.run_sample(Path(binary['path']), fixture, meter, spec,
                                      out / ('validate-' + label), args.timeout, args.sample_interval)
            sample.update(case=spec['name'], label=label, warmup=False)
            if sample['stdout']['sha256'] != calibration['text_output_sha256']:
                sample['issues'].append('text output differs from independently drained cached source plus newline')
            validation.append(sample)
        verdict = stress.summarize(validation, [spec], list(binary_ids))[spec['name']]
        report['output_validations'] = {'samples': validation, 'verdict': verdict,
            'scope': 'Go, baseline Rust and candidate Rust full-output parity plus independent source+newline hash'}
        if not verdict['valid'] or not verdict['compatible']:
            raise ValueError('Go/baseline/candidate output validation failed; timing withheld')
        validated = {'response_sha256': calibration['sha256'], 'stdout_sha256': validation[0]['stdout']['sha256'],
                     'stdout_bytes': validation[0]['stdout']['bytes'], 'scope': 'three-binary full pipe validation'}
        rng = random.Random(args.seed)
        for round_index in range(-args.warmup, args.repetitions):
            order = ['baseline', 'candidate']
            rng.shuffle(order)
            for label in order:
                sample = stress.run_sample(Path(binary_ids[label]['path']), fixture, meter, spec,
                    out / f'round-{round_index:+06d}-{label}', args.timeout, args.sample_interval,
                    stdout_sink='null', validated_source=validated)
                sample.update(case=spec['name'], label=label, round=round_index, warmup=round_index < 0)
                wall = sample['rates']['wall_ms']
                # Missing native metrics give zero wall; record, never divide.
                rate = manifest['body']['size_bytes'] / 1048576 * 1000 / wall if wall > 0 else None
                sample['producer_comparison'] = {'source_mib_per_second': rate,
                    'raw_drain_mib_per_second': calibration['source_mib_per_second'],
                    'possibly_producer_bound': rate >= calibration['source_mib_per_second'] * .9
                                               if rate is not None else None,
                    'calibration_reader': CALIBRATION_READER,
                    'scope': 'single cached raw-drain ceiling diagnostic'}
                if rate is None:
                    sample['issues'].append('nonpositive native wall time; producer comparison unmeasured')
                report['samples'].append(sample)
                harness.write_json(out / 'report.json', report)
                print(f"round={round_index} {label} cpu_ms={sample['rates']['cpu_ms']:.3f} issues={sample['issues']}", flush=True)
        summary = stress.summarize(report['samples'], [spec], ['baseline', 'candidate'])[spec['name']]
        selected = [s for s in report['samples'] if not s['warmup']]
        comparisons = [s['producer_comparison']['possibly_producer_bound'] for s in selected]
        summary['possibly_producer_bound_runs'] = sum(c is True for c in comparisons)
        summary['unmeasured_producer_comparison_runs'] = sum(c is None for c in comparisons)
        summary['performance_admitted'] &= not (summary['possibly_producer_bound_runs']
                                                or summary['unmeasured_producer_comparison_runs'])
        report['summary'], report['paired'] = summary, paired_summary(report['samples'])
        final_identity = stress.identity(args.manifest)
        report['manifest_identity_end'] = final_identity
        if final_identity != manifest_identity:
            report['errors'].append('manifest identity changed during run')
        report['complete'] = True
        report['passed'] = (summary['valid'] and summary['compatible'] and final_identity == manifest_identity
                            and all(not s['issues'] for s in report['samples']))
        return 0 if report['passed'] else 1
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        raise
    finally:
        harness.write_json(out / 'report.json', report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='internal fixture server entry point')
    sub = parser.add_subparsers(dest='command')
    prep = sub.add_parser('prepare', help='prepare immutable body outside measurements')
    prep.add_argument('--config', type=Path, required=True)
    prep.add_argument('--fixture', type=Path, default=Path('bin/pb-stress-fixture'))
    prep.add_argument('--out', type=Path, required=True)
    prep.add_argument('--timeout', type=stress.positive, default=300)
    measure = sub.add_parser('run', help='raw calibration, three-binary pipe parity, paired native null-sink capture')
    measure.add_argument('--manifest', type=Path, required=True)
    measure.add_argument('--go', type=Path, required=True)
    measure.add_argument('--reference', type=Path, required=True)
    measure.add_argument('--candidate', type=Path, required=True)
    measure.add_argument('--meter', type=Path, default=Path('bin/pb-meter'))
    measure.add_argument('--out', type=Path, required=True)
    measure.add_argument('--repetitions', type=stress.nonnegative_int, default=5)
    measure.add_argument('--warmup', type=stress.nonnegative_int, default=1)
    measure.add_argument('--seed', type=int, default=2063)
    measure.add_argument('--sample-interval', type=stress.positive, default=.05)
    measure.add_argument('--timeout', type=stress.positive, default=130)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        return prepare(args)
    if args.command == 'run':
        if not 1 <= args.repetitions <= 1000 or args.warmup > 100 or not .01 <= args.sample_interval <= 60:
            parser.error('require repetitions 1..1000, warmup<=100, sample interval .01..60')
        if not 1 <= args.timeout <= 86400:
            parser.error('timeout must be 1..86400 seconds')
        return run(args)
    if args.config:
        return serve(args.config)
    parser.error('require prepare, run, or --config')


if __name__ == '__main__':
    raise SystemExit(main())
