#!/usr/bin/env python3
"""Fixed latency and response-size workloads, separate from the parity corpus."""
import argparse
import copy
import json

import phase2


def suite():
    base = {case["name"]: case for case in phase2.benchmark_suite(phase2.corpus())}
    result = []

    def derive(source, name, group):
        case = copy.deepcopy(base[source])
        case.update(name=name, group=group)
        result.append(case)
        return case

    status = derive("p2-status-basic-json", "p2-status-delay-50ms", "phase2-latency")
    status["fixture"]["routes"][0]["delay_ms"] = 50
    info = derive("p2-dataset-info-json", "p2-info-four-delays-20ms", "phase2-latency")
    for route in info["fixture"]["routes"]:
        route["delay_ms"] = 20

    listing = derive("p2-dataset-list-basic-json", "p2-list-10000-datasets", "phase2-large")
    listing["fixture"]["routes"][0]["body"] = json.dumps({"datasets": [
        {"title": f"dataset-{i:05d}", "datasetType": "logs", "datasetFormat": "json", "ingestion": True}
        for i in reversed(range(10_000))
    ]}, separators=(",", ":"))

    schema = derive("p2-dataset-schema-json", "p2-schema-4096-fields-chunked", "phase2-large")
    schema["fixture"]["routes"][0].update(
        body=json.dumps({"fields": [
            {"name": f"field-{i:04d}", "data_type": "Utf8", "nullable": True}
            for i in range(4096)
        ]}, separators=(",", ":")),
        chunk_bytes=4096,
        chunk_delay_ms=2,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="new suite file; never overwritten")
    args = parser.parse_args()
    with open(args.out, "x") as stream:
        json.dump(suite(), stream, indent=2)
        stream.write("\n")
