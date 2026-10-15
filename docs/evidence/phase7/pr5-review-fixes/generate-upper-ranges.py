#!/usr/bin/env python3
"""Compress upper_oracle.go output into unicode.rs's UPPER constant."""
import pathlib
import sys
pairs = [tuple(int(c, 16) for c in line.split())
         for line in pathlib.Path(sys.argv[1]).read_text().splitlines()
         if not line.startswith('//')]
ranges = []
i = 0
while i < len(pairs):
    lo, target = pairs[i]
    delta, hi, stride = target - lo, lo, 1
    if (i + 1 < len(pairs) and pairs[i + 1][1] - pairs[i + 1][0] == delta
            and pairs[i + 1][0] - lo in (1, 2)):
        stride = pairs[i + 1][0] - lo
    j = i + 1
    while (j < len(pairs) and pairs[j][0] - hi == stride
           and pairs[j][1] - pairs[j][0] == delta):
        hi, j = pairs[j][0], j + 1
    ranges.append((lo, hi, stride, delta))
    i = j
print('const UPPER: &[(u32, u32, u32, i32)] = &[')
for lo, hi, stride, delta in ranges:
    print(f'    (0x{lo:X}, 0x{hi:X}, {stride}, {delta}),')
print('];')
