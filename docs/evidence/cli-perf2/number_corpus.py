#!/usr/bin/env python3
"""Generate an exact-output Go/Rust SQL number differential suite from repo root.

    python3 docs/evidence/cli-perf2/number_corpus.py /tmp/pb-number-suite.json
    python3 harness/run.py run --suite /tmp/pb-number-suite.json \
      --binary go=bin/pb-go --binary final=bin/pb-perf2-final \
      --fixture bin/pb-fixture --meter bin/pb-meter --warmup 0 --repetitions 1 \
      --out /tmp/pb-number-replay

This is a compatibility corpus, not a performance workload. Existing run.py
contracts compare exact normalized stdout/stderr and termination. Its phase4
raw-request hook additionally pins the SQL request path and exact body bytes.
Response numbers are joined as lexemes, never decoded/reencoded as Python floats.
"""
import argparse
import copy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
import sys

SEED = 0x50425F4E554D4245
VALUES_PER_ROW = 32


def corpus():
    values = {}  # Insertion order and deduplication are deterministic.

    def add(text):
        # Only use float for range classification; retain the original lexeme.
        if math.isfinite(float(text)):
            values.setdefault(text, None)

    def signed(text):
        add(text)
        add('-' + text)

    def decimal(digits, point):
        if point == 0:
            return '0.' + digits
        if point == len(digits):
            return digits
        return digits[:point] + '.' + digits[point:]

    # Dense neighboring coefficients on both sides of decimal-width boundaries.
    # Moving the point changes scale while retaining the same 14/15/16-digit span.
    for width in (14, 15, 16):
        for leading in range(1, 10):
            center = int(str(leading) + '9' * (width - 1))
            for coefficient in range(center - 32, center + 33):
                digits = str(coefficient)
                for point in (0, len(digits) // 2, len(digits)):
                    text = decimal(digits, point)
                    signed(text)
                    if coefficient % 8 == 0:
                        signed(text + ('000000' if '.' in text else '.000000'))
                if coefficient % 8 == 0:
                    for zeros in range(8):
                        signed('0.' + '0' * zeros + digits)

    rng = random.Random(SEED)
    for _ in range(12_000):
        width = rng.randrange(1, 25)
        digits = str(rng.randrange(1, 10)) + ''.join(str(rng.randrange(10)) for _ in range(width - 1))
        text = decimal(digits, rng.randrange(width + 1))
        if text.startswith('0.'):
            text = '0.' + '0' * rng.randrange(9) + text[2:]
        if '.' in text:
            text += '0' * rng.randrange(21)
        add(('-' if rng.randrange(2) else '') + text)
    for _ in range(3_000):
        digits = str(rng.randrange(1, 10**rng.randrange(1, 23)))
        text = decimal(digits, rng.randrange(len(digits) + 1))
        exponent = rng.randrange(-350, 301)
        text += rng.choice(('e', 'E')) + ('+' if exponent >= 0 else '-') + str(abs(exponent))
        add(('-' if rng.randrange(2) else '') + text)

    # Decimal/f64 notation cutoffs, integral precision, and neighboring doubles.
    for center in (10**14, 10**15, 10**16, 2**52, 2**53, 10**20, 10**21):
        for integer in range(center - 64, center + 65):
            signed(str(integer))
            signed(str(integer) + '.000')
    bounds = [1e-7, 1e-6, 1e-5, 1e20, 1e21, 1e22,
              float.fromhex('0x1p-1074'), float.fromhex('0x1p-1022'),
              float.fromhex('0x1.fffffffffffffp+1023')]
    bounds += [math.ldexp(1.0, power) for power in (-53, -52, -1, 0, 1, 49, 50, 51, 52, 53, 54, 69, 70)]
    for center in bounds:
        for value in (math.nextafter(center, 0), center, math.nextafter(center, math.inf)):
            if not math.isfinite(value):
                continue
            for text in (repr(value), format(value, '.15g'), format(value, '.17g'),
                         format(value, '.17f'), format(Decimal.from_float(value), 'f')):
                signed(text)
    for length in (0, 1, 5, 6, 7, 15, 16, 64, 300, 1074, 2048):
        signed('0' if length == 0 else '0.' + '0' * length)
        for digits in ('1', '123456789012345', '1234567890123456', '999999999999999'):
            signed('0.' + '0' * length + digits)
            signed('1.' + digits + '0' * length)
    for text in ('0e0', '0E+308', '0e-9999', '1.7976931348623157e308',
                 '1.7976931348623158e308', '2.2250738585072014e-308',
                 '4.9406564584124654e-324', '2.4703282292062327e-324'):
        signed(text)
    assert len(values) >= 20_000
    return list(values)


def make_case(template, name, lexemes, expected_exit=0):
    case = copy.deepcopy(template)
    chunks = [lexemes[i:i + VALUES_PER_ROW] for i in range(0, len(lexemes), VALUES_PER_ROW)]
    assert len(chunks) <= 10_000
    body = '[' + ','.join('{"sequence":%d,"numbers":[%s]}' % (i, ','.join(chunk))
                          for i, chunk in enumerate(chunks)) + ']'
    decoded = json.loads(body, parse_int=str, parse_float=str)
    assert [n for row in decoded for n in row['numbers']] == lexemes
    assert [row['sequence'] for row in decoded] == list(map(str, range(len(chunks))))
    case.update(name=name, group='number-corpus', expected_exit=expected_exit, rows=len(chunks))
    case['argv'][2] = 'SELECT * FROM logs LIMIT 10000'
    case['fixture']['routes'][0]['body'] = body
    request = json.dumps({'query': case['argv'][2], 'startTime': '2024-01-01T00:00:00Z',
                          'endTime': '2024-01-01T01:00:00Z'}, separators=(',', ':'))
    case['phase4'] = {'count': len(chunks), 'buffered': False, 'oracle_required': False,
                      'raw_requests': [{'method': 'POST', 'path': '/api/v1/query', 'body': request}]}
    case['number_corpus'] = {'seed': SEED, 'numeric_lexemes': len(lexemes),
                             'lexeme_sha256': hashlib.sha256('\n'.join(lexemes).encode()).hexdigest()}
    return case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd() / 'harness'))
    import cases
    template = next(case for case in cases.suite() if case['name'] == 'sql-500-json')
    lexemes = corpus()
    suite = [make_case(template, 'sql-number-corpus-json', lexemes)]
    # Separate overflow cases keep every finite row observable and check that
    # a later failing number retains Go's error and already-emitted prefix.
    for index, overflow in enumerate(('1e309', '-1e309', '1.7976931348623159e308', '-1.7976931348623159e308')):
        suite.append(make_case(template, 'sql-number-overflow-%d-json' % index,
                               ['0', '-0', '1.25000'] * VALUES_PER_ROW + [overflow], 1))
    args.out.write_text(json.dumps(suite, indent=2) + '\n')
    print(json.dumps({'suite': str(args.out), 'cases': len(suite), 'finite_lexemes': len(lexemes),
                      'finite_rows': suite[0]['rows'], 'lexeme_sha256': suite[0]['number_corpus']['lexeme_sha256']}))


if __name__ == '__main__':
    main()
