#!/usr/bin/env python3
"""Retain compact raw RSS diagnostic samples and hashes; never rank performance."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--samples', type=Path, required=True)
    args = parser.parse_args()
    records, phases = [], []
    for source in args.reports:
        report = json.loads(source.read_text())
        phase = {'report': str(source), 'report_sha256': sha(source),
                 'script_sha256': report['script_sha256'], 'passed': report['passed'],
                 'complete': report['complete'], 'configuration': report['configuration'],
                 'host': report['host'], 'originals': report['originals'],
                 'variants': report['variants'], 'meter': report['meter'],
                 'copy_storage': report.get('copy_storage'), 'series': {}}
        manifest = {'report': str(source), 'report_sha256': sha(source), 'artifacts': {}}
        phase_records = []
        for run in report['runs']:
            folder = source.parent / run['artifact']
            for artifact in sorted(folder.iterdir()):
                if artifact.is_file():
                    manifest['artifacts'][str(artifact.relative_to(source.parent))] = {
                        'sha256': sha(artifact), 'bytes': artifact.stat().st_size}
            snapshot = run.get('snapshot', {})
            total, groups = snapshot.get('total_kb', {}), snapshot.get('groups_kb', {})
            status, meter = snapshot.get('status_kb', {}), run.get('meter', {})
            row = {'phase': source.parent.name, 'variant': run['variant'],
                   'repetition': run['repetition'], 'order_in_round': run['order_in_round'],
                   'passed': run['passed'], 'error': run.get('error'),
                   'binary_sha256': run['binary']['sha256'], 'artifact': run['artifact'],
                   'run_json_sha256': sha(folder / 'run.json'),
                   'smaps_total_kb': total.get('Rss'), 'status_rss_kb': status.get('VmRSS'),
                   'status_hwm_kb': status.get('VmHWM'),
                   'native_peak_kb': meter.get('peak_rss_bytes', 0) / 1024 if meter else None,
                   'smaps_rx_kb': groups.get('executable:r-xp', {}).get('Rss'),
                   'smaps_ro_kb': groups.get('executable:r--p', {}).get('Rss'),
                   'smaps_anonymous_kb': total.get('Anonymous'),
                   'heap_kb': groups.get('heap', {}).get('Rss'),
                   'anon_huge_kb': total.get('AnonHugePages'), 'file_pmd_kb': total.get('FilePmdMapped'),
                   'native_minor_faults': meter.get('minor_faults')}
            for label, left, right in (
                ('smaps_minus_status_kb', 'smaps_total_kb', 'status_rss_kb'),
                ('hwm_minus_status_kb', 'status_hwm_kb', 'status_rss_kb'),
                ('native_minus_write_kb', 'native_peak_kb', 'smaps_total_kb'),
            ):
                row[label] = row[left] - row[right] if row[left] is not None and row[right] is not None else None
            preparation = run.get('private_residency_preparation') or {}
            for stage in ('post_write_fsync', 'post_private_dontneed', 'post_full_read'):
                row[stage + '_resident_pages'] = preparation.get(stage, {}).get('resident_pages')
                row[stage + '_total_pages'] = preparation.get(stage, {}).get('pages')
            records.append(row)
            phase_records.append(row)
        manifest_path = source.parent / 'artifact-manifest.json'
        write_json(manifest_path, manifest)
        phase.update(artifact_manifest=str(manifest_path), artifact_manifest_sha256=sha(manifest_path),
                     inputs_unchanged=report.get('inputs_unchanged'))
        for variant in report['variants']:
            selected = [r for r in phase_records if r['variant'] == variant and r['passed']]
            numeric = [k for k in records[0] if k.endswith(('_kb', '_pages', '_faults'))]
            metrics = {}
            for key in numeric:
                values = [r[key] for r in selected if r[key] is not None]
                if values:
                    metrics[key] = {'median': statistics.median(values), 'min': min(values), 'max': max(values)}
            phase['series'][variant] = {'passing_runs': len(selected), 'metrics': metrics}
        phases.append(phase)
    with args.samples.open('w', newline='') as dest:
        writer = csv.DictWriter(dest, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    summary = {'kind': 'RSS attribution diagnostics; original benchmark rankings remain unchanged',
               'samples_csv': str(args.samples), 'samples_csv_sha256': sha(args.samples),
               'samples': len(records), 'successful_samples': sum(r['passed'] for r in records),
               'kernel_documentation': 'https://www.kernel.org/doc/html/latest/filesystems/proc.html',
               'limitations': [
                   'Full-pipe snapshots are precise RSS at the natural output-write boundary, not lifetime peaks.',
                   'Native wait4 maxrss is collected after release and can exceed snapshot RSS as exit paths execute.',
                   'File-copy/inode sensitivity is established; no exact kernel compression/folio mechanism is inferred.',
                   'W/R changes only private-copy cache history; mincore confirms presence, not folio order.',
                   'Tmpfs controls are explanatory controls and do not replace original-path measured regressions.',
                   'Initial failed phase retained: Btrfs stat device differs from mapping device; pathname/inode fallback fixed attribution.',
               ], 'phases': phases}
    write_json(args.out, summary)
    print(json.dumps({'summary': str(args.out), 'samples': len(records),
                      'successful_samples': summary['successful_samples']}))


if __name__ == '__main__':
    main()
