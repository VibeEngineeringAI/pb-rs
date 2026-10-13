#!/usr/bin/env python3
"""Fixed SQL size/latency workloads; never derives coverage from candidate output."""
import argparse
import copy
import json

import cases


def suite():
    existing = {case["name"]: case for case in cases.suite()}
    result = []

    def add(name, body, rows, output="json", **route_options):
        case = copy.deepcopy(existing[f"sql-1-{output}"])
        case.update(name=name, group="phase3-workloads", rows=rows,
                    json_array_rows=rows, max_output_bytes=100 << 20)
        case["argv"][2] = f"SELECT * FROM logs LIMIT {rows}"
        route = case["fixture"]["routes"][0]
        route.update(body=body, **route_options)
        case["fixture_response_body_bytes"] = len(body.encode("utf-8"))
        result.append(case)
        return case

    def body(rows):
        return json.dumps([{"sequence": i, "message": "fixture <event> café 日志",
                            "large_integer": 9007199254740993 + i,
                            "value": i / 4, "optional": None}
                           for i in range(rows)], ensure_ascii=False, separators=(",", ":"))

    for rows in (1, 500, 1000, 10000, 100000):
        payload = body(rows)
        for output in ("text", "json"):
            add(f"p3-{rows}-{output}", payload, rows, output)
    add("p3-headers-delay-50ms", body(1), 1, delay_ms=50)
    add("p3-chunks-delay-2ms", body(1000), 1000, chunk_bytes=4096, chunk_delay_ms=2)
    slow = add("p3-slow-reader-10ms", body(10000), 10000)
    slow["read_delay_ms"] = 10
    large_item = add("p3-single-8MiB-item", json.dumps([{"sequence": 0, "message": "x" * (8 << 20)}]), 1)
    # Python validation in the drain loop would throttle this entire first item.
    # Count/order validation still runs after the child exits; timing is N/A.
    large_item["time_first_json_element"] = False
    leading_whitespace = add("p3-leading-1MiB-whitespace", " " * (1 << 20) + body(1), 1)
    leading_whitespace["time_first_json_element"] = False
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with open(args.out, "x") as stream:
        json.dump(suite(), stream, indent=2)
        stream.write("\n")
