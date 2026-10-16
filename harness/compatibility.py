"""Project the declared Rust-only ingest/observe additions out of Go comparisons.

All retained bytes, ordering, spacing, escaping, exits and effects stay exact.
The original process output is always retained by callers. This is deliberately
an explicit extension list, independent of the candidate's command declarations.
"""

import json
import re


PROJECTION = "rust-ingest-observe-v1"
COMMANDS = {
    "ingest": "Ingest events into Parseable",
    "observe": "Read session events with an explicit cursor receipt",
}
OBSERVE_CATALOG_COMMAND = "pb observe <dataset> --session <session> --cursor-file <path> -o json"
OBSERVE_CATALOG_DESCRIPTION = "Read bounded session events and acknowledge receipts with a local cursor"
OBSERVE_NOTE = "pb observe emits JSON by default and writes local cursor receipts; --ack TOKEN advances only the local cursor"
ANSI = re.compile(rb"\x1b\[[0-9;]*m")


def _remove_array_entries(text, key, predicate, indent="  "):
    """Delete selected array items without reserializing any retained JSON."""
    marker = indent + json.dumps(key) + ": ["
    start = text.find("\n" + marker)
    if start < 0:
        return text
    start += len("\n" + marker)
    decoder = json.JSONDecoder()
    entries = []
    pos = start
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if text[pos] == "]":
            break
        value, end = decoder.raw_decode(text, pos)
        entries.append((pos, end, value))
        pos = end
        while text[pos].isspace():
            pos += 1
        if text[pos] == ",":
            pos += 1
        elif text[pos] != "]":
            raise ValueError("invalid JSON array")
    for index, (first, last, value) in enumerate(entries):
        if not predicate(value):
            continue
        if index + 1 < len(entries):
            left = text.rfind("\n", 0, first) + 1
            right = text.rfind("\n", 0, entries[index + 1][0]) + 1
        elif index:
            left, right = entries[index - 1][1], last
        else:
            left, right = start, last
        # Re-read offsets after each removal, including adjacent additions.
        return _remove_array_entries(text[:left] + text[right:], key, predicate, indent)
    return text


def project(data, argv=()):
    """Remove only known extension entries from root help/discovery/catalog."""
    try:
        text = data.decode("utf-8")
        value = json.loads(text)
        if isinstance(value, dict) and value.get("command") == "pb":
            text = _remove_array_entries(text, "subcommands", lambda child:
                isinstance(child, dict) and child.get("command") in ("pb ingest", "pb observe"))
        elif isinstance(value, dict) and value.get("catalog") == "cli-declared read-only commands":
            text = _remove_array_entries(text, "commands", lambda child:
                isinstance(child, dict) and child.get("command") == OBSERVE_CATALOG_COMMAND)
            text = _remove_array_entries(text, "special_cases", lambda note: note == OBSERVE_NOTE, "    ")
        else:
            return data
        return text.encode("utf-8")
    except (ValueError, UnicodeError, IndexError):
        pass
    # Root help may be colored. Match the full plain row but retain every byte
    # of all other rows, including ANSI styling and padding.
    root_help = b"Available Commands:" in ANSI.sub(b"", data) and b"  pb [command] [flags]\n" in data
    root_usage = b"Usage:\n  pb [flags]\n  pb [command]\n" in data and b"Available Commands:\n" in data
    catalog = data.startswith(b"Read-only command catalog for agents\n")
    completion = (len(argv) == 2 and argv[0] in ("__complete", "__completeNoDesc")
                  and bool(re.search(rb"\n:[0-9]+\n$", data)))
    out = []
    for line in data.splitlines(keepends=True):
        plain = ANSI.sub(b"", line)
        width = 10 if root_help else 12
        if (root_help or root_usage) and any(plain == f"  {name}{' ' * (width - len(name))}{description}\n".encode()
                                           for name, description in COMMANDS.items()):
            continue
        if completion and any(plain in (f"{name}\n".encode(), f"{name}\t{description}\n".encode())
                              for name, description in COMMANDS.items()):
            continue
        if catalog and plain == f"- {OBSERVE_CATALOG_COMMAND}: {OBSERVE_CATALOG_DESCRIPTION}\n".encode():
            continue
        out.append(line)
    return b"".join(out)
