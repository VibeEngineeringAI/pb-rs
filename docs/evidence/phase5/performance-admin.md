# Phase-five administration workloads

Warm-cache, fresh-process sequential samples; seeded randomized binary order per round.
Fixture state/config reset before every warmup and sample (control); setup, reset, ledger capture and artifacts are outside timing.
Fan-out siblings are compared as a multiset and map-ordered role sequences with multiplicity; everything else is exact.
p95 from 30 samples is descriptive; bootstrap intervals are pointwise and exploratory.

## go-self-comparison (passed)

Purpose: Go A/A correctness gate; 0 warmup and 2 measured rounds per binary.
Workloads with raw order/output variation between samples: 20; skipped (failures): none.

| Workload | Binary | Valid | Wall median / p95 ms | CV | CPU ms | Peak RSS MiB | First request ms | Request→stdout ms | Max in-flight | Requests | 401s (max) | Logical bytes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| p5-user-list-small-text | go-a | 2/2 | 31.240 / 32.237 | 0.050 | 37.633 | 53.689 | 26.637 | 3.529 | 2 | 9 | 0 | 2862 |
| p5-user-list-small-text | go-b | 2/2 | 35.044 / 37.107 | 0.092 | 45.333 | 53.074 | 30.016 | 3.345 | 2 | 9 | 0 | 2862 |
| p5-role-list-small-text | go-a | 2/2 | 33.484 / 33.497 | 0.001 | 42.183 | 53.104 | 29.407 | 2.258 | 2 | 6 | 0 | 1216 |
| p5-role-list-small-text | go-b | 2/2 | 28.194 / 28.430 | 0.013 | 37.313 | 52.648 | 24.978 | 2.051 | 2 | 6 | 0 | 1216 |
| p5-user-list-small-json | go-a | 2/2 | 32.729 / 32.754 | 0.001 | 41.205 | 53.152 | 27.791 | 3.757 | 2 | 9 | 0 | 3289 |
| p5-user-list-small-json | go-b | 2/2 | 31.664 / 32.418 | 0.037 | 41.392 | 53.430 | 26.472 | 3.289 | 2 | 9 | 0 | 3289 |
| p5-role-list-small-json | go-a | 2/2 | 32.482 / 32.973 | 0.024 | 40.909 | 52.746 | 29.019 | 1.987 | 2 | 6 | 0 | 1382 |
| p5-role-list-small-json | go-b | 2/2 | 34.729 / 35.435 | 0.032 | 43.145 | 53.273 | 30.965 | 2.419 | 2 | 6 | 0 | 1382 |
| p5-user-list-small-detail-failure-text | go-a | 2/2 | 32.071 / 32.119 | 0.002 | 40.876 | 53.287 | 27.206 | 3.205 | 2 | 9 | 0 | 2739 |
| p5-user-list-small-detail-failure-text | go-b | 2/2 | 32.700 / 32.746 | 0.002 | 41.561 | 53.104 | 27.787 | 3.320 | 2 | 9 | 0 | 2739 |
| p5-user-list-small-detail-failure-json | go-a | 2/2 | 31.696 / 34.136 | 0.121 | 40.687 | 53.637 | 26.866 | N/A | 3 | 9 | 0 | 2326 |
| p5-user-list-small-detail-failure-json | go-b | 2/2 | 32.759 / 35.298 | 0.122 | 41.009 | 53.170 | 27.791 | N/A | 2 | 9 | 0 | 2326 |
| p5-role-list-small-detail-failure-json | go-a | 2/2 | 41.981 / 43.391 | 0.053 | 45.271 | 53.426 | 37.827 | N/A | 1 | 6 | 0 | 895 |
| p5-role-list-small-detail-failure-json | go-b | 2/2 | 29.666 / 30.740 | 0.057 | 37.677 | 52.832 | 26.017 | N/A | 1 | 6 | 0 | 895 |
| p5-user-list-large-text | go-a | 2/2 | 519.969 / 521.148 | 0.004 | 457.454 | 101.758 | 25.825 | 483.441 | 30 | 1001 | 0 | 3693576 |
| p5-user-list-large-text | go-b | 2/2 | 550.412 / 558.430 | 0.023 | 481.328 | 103.543 | 29.143 | 511.690 | 24 | 1001 | 0 | 3693576 |
| p5-role-list-large-text | go-a | 2/2 | 166.410 / 166.471 | 0.001 | 156.676 | 64.162 | 27.611 | 110.999 | 26 | 251 | 0 | 579299 |
| p5-role-list-large-text | go-b | 2/2 | 160.865 / 162.376 | 0.015 | 157.024 | 63.756 | 33.423 | 95.961 | 14 | 251 | 0 | 579299 |
| p5-user-list-large-json | go-a | 2/2 | 572.142 / 612.238 | 0.110 | 461.201 | 104.521 | 31.647 | 535.250 | 32 | 1001 | 0 | 3765908 |
| p5-user-list-large-json | go-b | 2/2 | 550.660 / 566.882 | 0.046 | 462.542 | 103.982 | 30.245 | 515.223 | 39 | 1001 | 0 | 3765908 |
| p5-role-list-large-json | go-a | 2/2 | 133.077 / 134.532 | 0.017 | 126.220 | 65.324 | 26.735 | 103.830 | 22 | 251 | 0 | 776425 |
| p5-role-list-large-json | go-b | 2/2 | 147.232 / 148.375 | 0.012 | 142.935 | 65.201 | 34.425 | 110.045 | 15 | 251 | 0 | 776425 |
| p5-user-list-large-skewed-json | go-a | 2/2 | 635.663 / 644.817 | 0.023 | 440.206 | 103.457 | 28.998 | 602.586 | 37 | 1001 | 0 | 3765908 |
| p5-user-list-large-skewed-json | go-b | 2/2 | 653.827 / 669.145 | 0.037 | 461.245 | 102.871 | 28.793 | 620.242 | 35 | 1001 | 0 | 3765908 |
| p5-role-list-large-skewed-json | go-a | 2/2 | 264.324 / 274.502 | 0.061 | 134.002 | 65.836 | 27.941 | 233.219 | 27 | 251 | 0 | 776425 |
| p5-role-list-large-skewed-json | go-b | 2/2 | 247.749 / 256.104 | 0.053 | 133.214 | 64.740 | 30.211 | 214.537 | 29 | 251 | 0 | 776425 |
| p5-user-list-large-detail-failure-text | go-a | 2/2 | 531.519 / 532.869 | 0.004 | 472.162 | 102.404 | 27.436 | 492.886 | 21 | 1001 | 0 | 3659595 |
| p5-user-list-large-detail-failure-text | go-b | 2/2 | 512.684 / 524.891 | 0.037 | 453.373 | 102.805 | 25.897 | 477.449 | 34 | 1001 | 0 | 3659595 |
| p5-user-list-large-detail-failure-json | go-a | 2/2 | 542.967 / 548.886 | 0.017 | 446.457 | 103.311 | 32.173 | N/A | 30 | 1001 | 0 | 3599217 |
| p5-user-list-large-detail-failure-json | go-b | 2/2 | 535.219 / 536.315 | 0.003 | 449.373 | 101.943 | 28.859 | N/A | 23 | 1001 | 0 | 3599217 |
| p5-role-list-large-detail-failure-json | go-a | 2/2 | 145.115 / 156.318 | 0.121 | 118.284 | 63.387 | 32.258 | N/A | 15 | 251 | 0 | 414998 |
| p5-role-list-large-detail-failure-json | go-b | 2/2 | 145.499 / 146.200 | 0.008 | 132.660 | 63.006 | 32.185 | N/A | 13 | 251 | 0 | 414998 |
| p5-dataset-add-logs | go-a | 2/2 | 29.380 / 32.573 | 0.171 | 37.368 | 52.586 | 27.520 | 0.283 | 1 | 1 | 0 | 28 |
| p5-dataset-add-logs | go-b | 2/2 | 31.389 / 32.725 | 0.067 | 40.485 | 52.318 | 29.661 | 0.268 | 1 | 1 | 0 | 28 |
| p5-dataset-add-metrics | go-a | 2/2 | 28.189 / 28.302 | 0.006 | 36.870 | 52.484 | 26.212 | 0.312 | 1 | 1 | 0 | 31 |
| p5-dataset-add-metrics | go-b | 2/2 | 32.574 / 34.267 | 0.082 | 39.695 | 52.746 | 31.063 | 0.258 | 1 | 1 | 0 | 31 |
| p5-dataset-add-traces | go-a | 2/2 | 27.834 / 29.529 | 0.096 | 36.762 | 52.701 | 26.660 | 0.240 | 1 | 1 | 0 | 30 |
| p5-dataset-add-traces | go-b | 2/2 | 33.118 / 37.131 | 0.190 | 42.253 | 52.047 | 31.262 | 0.262 | 1 | 1 | 0 | 30 |
| p5-dataset-add-status-only-slow-body | go-a | 2/2 | 30.716 / 31.558 | 0.043 | 39.153 | 52.779 | 30.331 | 0.259 | 1 | 1 | 0 | 32799 |
| p5-dataset-add-status-only-slow-body | go-b | 2/2 | 34.669 / 36.799 | 0.097 | 42.704 | 52.119 | 32.850 | 0.261 | 1 | 1 | 0 | 32799 |
| p5-dataset-add-conflict | go-a | 2/2 | 28.944 / 30.079 | 0.062 | 37.788 | 52.570 | 27.407 | N/A | 1 | 1 | 0 | 90 |
| p5-dataset-add-conflict | go-b | 2/2 | 34.670 / 37.394 | 0.123 | 43.981 | 52.826 | 33.544 | N/A | 1 | 1 | 0 | 90 |
| p5-dataset-add-non200-2xx | go-a | 2/2 | 30.657 / 30.987 | 0.017 | 37.915 | 52.518 | 28.950 | N/A | 1 | 1 | 0 | 58 |
| p5-dataset-add-non200-2xx | go-b | 2/2 | 28.362 / 29.973 | 0.089 | 37.113 | 51.875 | 26.940 | N/A | 1 | 1 | 0 | 58 |
| p5-dataset-remove-guarded | go-a | 2/2 | 28.424 / 30.820 | 0.132 | 37.539 | 52.152 | 26.992 | 0.654 | 1 | 2 | 0 | 99 |
| p5-dataset-remove-guarded | go-b | 2/2 | 34.581 / 35.024 | 0.020 | 43.032 | 52.646 | 33.078 | 0.584 | 1 | 2 | 0 | 99 |
| p5-dataset-remove-guarded-mismatch | go-a | 2/2 | 32.671 / 35.816 | 0.151 | 41.675 | 52.486 | 30.775 | N/A | 1 | 1 | 0 | 101 |
| p5-dataset-remove-guarded-mismatch | go-b | 2/2 | 30.641 / 33.004 | 0.121 | 38.821 | 52.346 | 29.201 | N/A | 1 | 1 | 0 | 101 |
| p5-dataset-remove-unguarded | go-a | 2/2 | 27.078 / 29.693 | 0.152 | 35.362 | 51.840 | 26.032 | 0.247 | 1 | 1 | 0 | 44 |
| p5-dataset-remove-unguarded | go-b | 2/2 | 27.526 / 29.725 | 0.126 | 37.111 | 52.537 | 25.833 | 0.259 | 1 | 1 | 0 | 44 |
| p5-dataset-remove-missing | go-a | 2/2 | 29.381 / 31.369 | 0.106 | 36.894 | 51.971 | 28.073 | N/A | 1 | 1 | 0 | 78 |
| p5-dataset-remove-missing | go-b | 2/2 | 31.247 / 33.346 | 0.106 | 40.383 | 52.387 | 29.873 | N/A | 1 | 1 | 0 | 78 |
| p5-dataset-remove-status-only-slow-body | go-a | 2/2 | 32.499 / 36.691 | 0.203 | 43.501 | 52.785 | 31.744 | 0.316 | 1 | 1 | 0 | 32812 |
| p5-dataset-remove-status-only-slow-body | go-b | 2/2 | 31.670 / 35.691 | 0.199 | 38.639 | 52.902 | 29.940 | 0.274 | 1 | 1 | 0 | 32812 |
| p5-user-add-small | go-a | 2/2 | 30.161 / 30.461 | 0.016 | 38.086 | 52.031 | 28.370 | 1.012 | 1 | 3 | 0 | 811 |
| p5-user-add-small | go-b | 2/2 | 32.743 / 35.109 | 0.114 | 41.789 | 53.154 | 30.762 | 0.786 | 1 | 3 | 0 | 811 |
| p5-user-set-role-small | go-a | 2/2 | 29.176 / 30.201 | 0.055 | 36.800 | 52.094 | 27.725 | 0.832 | 1 | 3 | 0 | 760 |
| p5-user-set-role-small | go-b | 2/2 | 29.461 / 29.793 | 0.018 | 39.095 | 52.859 | 27.028 | 0.980 | 1 | 3 | 0 | 760 |
| p5-user-add-large | go-a | 2/2 | 38.396 / 42.828 | 0.181 | 42.944 | 53.703 | 29.917 | 6.973 | 1 | 3 | 0 | 245694 |
| p5-user-add-large | go-b | 2/2 | 33.753 / 34.271 | 0.024 | 41.215 | 53.352 | 25.960 | 6.576 | 1 | 3 | 0 | 245694 |
| p5-user-set-role-large | go-a | 2/2 | 35.081 / 35.146 | 0.003 | 40.477 | 53.623 | 27.117 | 6.784 | 1 | 3 | 0 | 245643 |
| p5-user-set-role-large | go-b | 2/2 | 44.153 / 44.304 | 0.005 | 50.597 | 53.639 | 34.381 | 8.268 | 1 | 3 | 0 | 245643 |
| p5-user-add-duplicate-roles | go-a | 2/2 | 33.753 / 39.806 | 0.282 | 42.240 | 52.896 | 31.435 | 0.922 | 1 | 3 | 0 | 834 |
| p5-user-add-duplicate-roles | go-b | 2/2 | 33.450 / 36.611 | 0.148 | 41.270 | 52.914 | 31.204 | 0.954 | 1 | 3 | 0 | 834 |
| p5-user-add-read-body-slow | go-a | 2/2 | 184.202 / 186.020 | 0.016 | 44.008 | 52.562 | 31.330 | 151.227 | 1 | 3 | 0 | 8953 |
| p5-user-add-read-body-slow | go-b | 2/2 | 181.796 / 184.876 | 0.027 | 39.973 | 51.838 | 29.763 | 150.947 | 1 | 3 | 0 | 8953 |
| p5-user-add-existing | go-a | 2/2 | 28.666 / 29.575 | 0.050 | 38.005 | 52.834 | 26.903 | N/A | 1 | 1 | 0 | 618 |
| p5-user-add-existing | go-b | 2/2 | 40.550 / 42.717 | 0.084 | 45.287 | 52.828 | 39.655 | N/A | 1 | 1 | 0 | 618 |
| p5-user-add-missing-role | go-a | 2/2 | 28.063 / 28.835 | 0.043 | 36.211 | 52.746 | 26.264 | N/A | 1 | 2 | 0 | 1098 |
| p5-user-add-missing-role | go-b | 2/2 | 29.255 / 30.418 | 0.062 | 38.336 | 52.230 | 26.989 | N/A | 1 | 2 | 0 | 1098 |
| p5-user-add-cloud-rejected | go-a | 2/2 | 29.343 / 30.252 | 0.049 | 37.771 | 51.260 | N/A | N/A | 0 | 0 | 0 | 392 |
| p5-user-add-cloud-rejected | go-b | 2/2 | 29.399 / 29.733 | 0.018 | 37.639 | 52.059 | N/A | N/A | 0 | 0 | 0 | 392 |
| p5-user-set-role-missing-user | go-a | 2/2 | 28.275 / 29.492 | 0.068 | 38.389 | 51.807 | 27.108 | N/A | 1 | 1 | 0 | 526 |
| p5-user-set-role-missing-user | go-b | 2/2 | 31.851 / 32.747 | 0.044 | 40.946 | 52.303 | 29.928 | N/A | 1 | 1 | 0 | 526 |
| p5-user-set-role-server-error | go-a | 2/2 | 30.393 / 33.963 | 0.185 | 38.710 | 52.711 | 28.480 | N/A | 1 | 3 | 0 | 902 |
| p5-user-set-role-server-error | go-b | 2/2 | 28.227 / 29.632 | 0.078 | 35.130 | 52.574 | 26.487 | N/A | 1 | 3 | 0 | 902 |
| p5-user-remove | go-a | 2/2 | 29.812 / 30.695 | 0.047 | 40.419 | 53.025 | 28.926 | 0.248 | 1 | 1 | 0 | 24 |
| p5-user-remove | go-b | 2/2 | 29.415 / 29.493 | 0.004 | 38.668 | 52.100 | 27.787 | 0.251 | 1 | 1 | 0 | 24 |
| p5-user-remove-missing | go-a | 2/2 | 29.540 / 31.477 | 0.103 | 38.484 | 52.664 | 27.837 | N/A | 1 | 1 | 0 | 218 |
| p5-user-remove-missing | go-b | 2/2 | 30.012 / 30.514 | 0.026 | 36.944 | 52.379 | 28.783 | N/A | 1 | 1 | 0 | 218 |
| p5-user-remove-status-only-slow-body | go-a | 2/2 | 33.197 / 35.526 | 0.110 | 41.230 | 52.527 | 31.260 | 0.325 | 1 | 1 | 0 | 32792 |
| p5-user-remove-status-only-slow-body | go-b | 2/2 | 28.192 / 29.438 | 0.069 | 37.646 | 52.121 | 26.353 | 0.299 | 1 | 1 | 0 | 32792 |
| p5-role-remove-small | go-a | 2/2 | 31.487 / 32.393 | 0.045 | 38.362 | 53.057 | 29.187 | 0.546 | 1 | 2 | 0 | 399 |
| p5-role-remove-small | go-b | 2/2 | 30.127 / 31.240 | 0.058 | 37.773 | 52.158 | 28.611 | 0.497 | 1 | 2 | 0 | 399 |
| p5-role-remove-large | go-a | 2/2 | 34.692 / 36.193 | 0.068 | 38.877 | 53.180 | 25.909 | 4.933 | 1 | 2 | 0 | 207586 |
| p5-role-remove-large | go-b | 2/2 | 32.311 / 32.602 | 0.014 | 37.730 | 54.027 | 25.673 | 5.116 | 1 | 2 | 0 | 207586 |
| p5-role-remove-missing | go-a | 2/2 | 42.640 / 42.847 | 0.008 | 46.854 | 53.047 | 31.224 | N/A | 1 | 1 | 0 | 210530 |
| p5-role-remove-missing | go-b | 2/2 | 34.543 / 35.293 | 0.034 | 43.464 | 53.678 | 28.647 | N/A | 1 | 1 | 0 | 210530 |
| p5-role-remove-status-only-slow-body | go-a | 2/2 | 31.141 / 32.591 | 0.073 | 39.519 | 52.307 | 29.000 | 0.593 | 1 | 2 | 0 | 33167 |
| p5-role-remove-status-only-slow-body | go-b | 2/2 | 32.107 / 35.588 | 0.170 | 39.668 | 52.459 | 30.102 | 0.628 | 1 | 2 | 0 | 33167 |
| p5-refresh-user-list-small-json | go-a | 2/2 | 35.461 / 38.205 | 0.122 | 42.692 | 51.184 | 29.203 | 4.292 | 2 | 11 | 1 | 3464 |
| p5-refresh-user-list-small-json | go-b | 2/2 | 38.141 / 40.721 | 0.106 | 44.380 | 51.309 | 31.966 | 4.420 | 2 | 11 | 1 | 3464 |
| p5-refresh-user-list-large-json | go-a | 2/2 | 554.475 / 569.895 | 0.044 | 446.121 | 97.354 | 33.232 | 516.134 | 28 | 1003 | 1 | 3766083 |
| p5-refresh-user-list-large-json | go-b | 2/2 | 561.716 / 572.176 | 0.029 | 494.609 | 100.076 | 32.358 | 524.163 | 45 | 1003 | 1 | 3766083 |
| p5-refresh-user-list-large-contention-json | go-a | 2/2 | 817.553 / 875.683 | 0.112 | 582.219 | 106.379 | 32.164 | 779.869 | 20 | 1821 | 1000 | 3783261 |
| p5-refresh-user-list-large-contention-json | go-b | 2/2 | 820.436 / 896.868 | 0.146 | 566.341 | 108.762 | 30.074 | 784.181 | 29 | 1724 | 1000 | 3781224 |
| p5-refresh-role-list-large-contention-json | go-a | 2/2 | 226.709 / 233.252 | 0.045 | 156.952 | 65.156 | 35.149 | 188.565 | 4 | 467 | 250 | 781094 |
| p5-refresh-role-list-large-contention-json | go-b | 2/2 | 243.867 / 243.889 | 0.000 | 164.785 | 64.576 | 37.496 | 202.750 | 4 | 486 | 250 | 781493 |
| p5-refresh-dataset-add | go-a | 2/2 | 44.846 / 53.498 | 0.303 | 45.319 | 50.973 | 40.805 | 1.439 | 1 | 3 | 1 | 205 |
| p5-refresh-dataset-add | go-b | 2/2 | 38.110 / 38.497 | 0.016 | 47.199 | 50.785 | 34.918 | 1.510 | 1 | 3 | 1 | 205 |
| p5-refresh-set-role-replay | go-a | 2/2 | 32.861 / 33.235 | 0.018 | 39.065 | 50.510 | 28.998 | 1.901 | 1 | 5 | 1 | 959 |
| p5-refresh-set-role-replay | go-b | 2/2 | 33.843 / 34.381 | 0.025 | 39.084 | 50.682 | 30.145 | 2.084 | 1 | 5 | 1 | 959 |
| p5-refresh-rejected-no-commit | go-a | 2/2 | 35.716 / 39.146 | 0.151 | 42.972 | 50.445 | 32.657 | N/A | 1 | 2 | 1 | 413 |
| p5-refresh-rejected-no-commit | go-b | 2/2 | 34.888 / 37.241 | 0.106 | 41.594 | 50.869 | 32.581 | N/A | 1 | 2 | 1 | 413 |

### A/A ratios (correctness gate; too few samples for inference)

- p5-user-list-small-text: go-b/go-a — wall_ms 1.122× [1.013, 1.239], Δ +3.804; cpu_ms 1.205× [1.158, 1.254], Δ +7.700; peak_rss_bytes 0.989× [0.979, 0.998], Δ -645120.000
- p5-role-list-small-text: go-b/go-a — wall_ms 0.842× [0.834, 0.850], Δ -5.289; cpu_ms 0.885× [0.834, 0.938], Δ -4.870; peak_rss_bytes 0.991× [0.986, 0.996], Δ -477184.000
- p5-user-list-small-json: go-b/go-a — wall_ms 0.967× [0.941, 0.994], Δ -1.065; cpu_ms 1.005× [0.981, 1.028], Δ +0.188; peak_rss_bytes 1.005× [1.003, 1.007], Δ +290816.000
- p5-role-list-small-json: go-b/go-a — wall_ms 1.069× [1.028, 1.112], Δ +2.247; cpu_ms 1.055× [0.958, 1.158], Δ +2.237; peak_rss_bytes 1.010× [1.005, 1.015], Δ +552960.000
- p5-user-list-small-detail-failure-text: go-b/go-a — wall_ms 1.020× [1.016, 1.023], Δ +0.629; cpu_ms 1.017× [1.007, 1.027], Δ +0.685; peak_rss_bytes 0.997× [0.994, 1.000], Δ -192512.000
- p5-user-list-small-detail-failure-json: go-b/go-a — wall_ms 1.034× [0.870, 1.228], Δ +1.063; cpu_ms 1.008× [0.901, 1.127], Δ +0.322; peak_rss_bytes 0.991× [0.986, 0.997], Δ -489472.000
- p5-role-list-small-detail-failure-json: go-b/go-a — wall_ms 0.707× [0.654, 0.764], Δ -12.315; cpu_ms 0.832× [0.794, 0.873], Δ -7.594; peak_rss_bytes 0.989× [0.978, 0.999], Δ -622592.000
- p5-user-list-large-text: go-b/go-a — wall_ms 1.059× [1.039, 1.078], Δ +30.443; cpu_ms 1.052× [0.997, 1.108], Δ +23.874; peak_rss_bytes 1.018× [0.994, 1.042], Δ +1871872.000
- p5-role-list-large-text: go-b/go-a — wall_ms 0.967× [0.956, 0.977], Δ -5.545; cpu_ms 1.002× [0.979, 1.026], Δ +0.348; peak_rss_bytes 0.994× [0.978, 1.010], Δ -425984.000
- p5-user-list-large-json: go-b/go-a — wall_ms 0.962× [0.864, 1.078], Δ -21.482; cpu_ms 1.003× [0.972, 1.035], Δ +1.340; peak_rss_bytes 0.995× [0.985, 1.005], Δ -565248.000
- p5-role-list-large-json: go-b/go-a — wall_ms 1.106× [1.084, 1.130], Δ +14.155; cpu_ms 1.132× [1.107, 1.158], Δ +16.715; peak_rss_bytes 0.998× [0.985, 1.011], Δ -129024.000
- p5-user-list-large-skewed-json: go-b/go-a — wall_ms 1.029× [0.986, 1.073], Δ +18.164; cpu_ms 1.048× [1.017, 1.079], Δ +21.039; peak_rss_bytes 0.994× [0.971, 1.017], Δ -614400.000
- p5-role-list-large-skewed-json: go-b/go-a — wall_ms 0.937× [0.865, 1.016], Δ -16.575; cpu_ms 0.994× [0.974, 1.014], Δ -0.787; peak_rss_bytes 0.983× [0.974, 0.992], Δ -1148928.000
- p5-user-list-large-detail-failure-text: go-b/go-a — wall_ms 0.965× [0.936, 0.993], Δ -18.835; cpu_ms 0.960× [0.948, 0.972], Δ -18.789; peak_rss_bytes 1.004× [0.978, 1.030], Δ +419840.000
- p5-user-list-large-detail-failure-json: go-b/go-a — wall_ms 0.986× [0.972, 1.000], Δ -7.749; cpu_ms 1.007× [0.963, 1.052], Δ +2.916; peak_rss_bytes 0.987× [0.980, 0.994], Δ -1433600.000
- p5-role-list-large-detail-failure-json: go-b/go-a — wall_ms 1.003× [0.918, 1.103], Δ +0.385; cpu_ms 1.122× [1.063, 1.185], Δ +14.376; peak_rss_bytes 0.994× [0.988, 1.000], Δ -399360.000
- p5-dataset-add-logs: go-b/go-a — wall_ms 1.068× [0.908, 1.273], Δ +2.009; cpu_ms 1.083× [0.997, 1.186], Δ +3.117; peak_rss_bytes 0.995× [0.989, 1.001], Δ -280576.000
- p5-dataset-add-metrics: go-b/go-a — wall_ms 1.156× [1.084, 1.228], Δ +4.386; cpu_ms 1.077× [1.006, 1.151], Δ +2.825; peak_rss_bytes 1.005× [1.001, 1.009], Δ +274432.000
- p5-dataset-add-traces: go-b/go-a — wall_ms 1.190× [0.964, 1.448], Δ +5.284; cpu_ms 1.149× [0.990, 1.322], Δ +5.491; peak_rss_bytes 0.988× [0.982, 0.993], Δ -686080.000
- p5-dataset-add-status-only-slow-body: go-b/go-a — wall_ms 1.129× [1.021, 1.244], Δ +3.953; cpu_ms 1.091× [1.010, 1.180], Δ +3.550; peak_rss_bytes 0.987× [0.983, 0.992], Δ -692224.000
- p5-dataset-add-conflict: go-b/go-a — wall_ms 1.198× [1.048, 1.362], Δ +5.726; cpu_ms 1.164× [1.127, 1.201], Δ +6.194; peak_rss_bytes 1.005× [0.992, 1.018], Δ +268288.000
- p5-dataset-add-non200-2xx: go-b/go-a — wall_ms 0.925× [0.857, 0.995], Δ -2.295; cpu_ms 0.979× [0.927, 1.031], Δ -0.802; peak_rss_bytes 0.988× [0.972, 1.004], Δ -673792.000
- p5-dataset-remove-guarded: go-b/go-a — wall_ms 1.217× [1.097, 1.361], Δ +6.157; cpu_ms 1.146× [1.069, 1.234], Δ +5.493; peak_rss_bytes 1.009× [1.001, 1.018], Δ +518144.000
- p5-dataset-remove-guarded-mismatch: go-b/go-a — wall_ms 0.938× [0.775, 1.140], Δ -2.030; cpu_ms 0.932× [0.797, 1.084], Δ -2.854; peak_rss_bytes 0.997× [0.990, 1.004], Δ -147456.000
- p5-dataset-remove-unguarded: go-b/go-a — wall_ms 1.017× [0.837, 1.240], Δ +0.448; cpu_ms 1.049× [0.911, 1.220], Δ +1.749; peak_rss_bytes 1.013× [1.008, 1.019], Δ +731136.000
- p5-dataset-remove-missing: go-b/go-a — wall_ms 1.064× [0.915, 1.236], Δ +1.866; cpu_ms 1.095× [0.996, 1.210], Δ +3.489; peak_rss_bytes 1.008× [0.997, 1.019], Δ +436224.000
- p5-dataset-remove-status-only-slow-body: go-b/go-a — wall_ms 0.974× [0.732, 1.298], Δ -0.829; cpu_ms 0.888× [0.714, 1.092], Δ -4.862; peak_rss_bytes 1.002× [0.984, 1.021], Δ +122880.000
- p5-user-add-small: go-b/go-a — wall_ms 1.086× [0.988, 1.186], Δ +2.582; cpu_ms 1.097× [0.995, 1.212], Δ +3.703; peak_rss_bytes 1.022× [1.001, 1.043], Δ +1177600.000
- p5-user-set-role-small: go-b/go-a — wall_ms 1.010× [0.960, 1.064], Δ +0.285; cpu_ms 1.062× [1.010, 1.116], Δ +2.295; peak_rss_bytes 1.015× [1.006, 1.023], Δ +802816.000
- p5-user-add-large: go-b/go-a — wall_ms 0.879× [0.766, 1.026], Δ -4.643; cpu_ms 0.960× [0.865, 1.070], Δ -1.728; peak_rss_bytes 0.993× [0.989, 0.998], Δ -368640.000
- p5-user-set-role-large: go-b/go-a — wall_ms 1.259× [1.251, 1.266], Δ +9.071; cpu_ms 1.250× [1.173, 1.328], Δ +10.120; peak_rss_bytes 1.000× [0.995, 1.005], Δ +16384.000
- p5-user-add-duplicate-roles: go-b/go-a — wall_ms 0.991× [0.740, 1.368], Δ -0.303; cpu_ms 0.977× [0.749, 1.295], Δ -0.971; peak_rss_bytes 1.000× [0.995, 1.006], Δ +18432.000
- p5-user-add-read-body-slow: go-b/go-a — wall_ms 0.987× [0.958, 1.017], Δ -2.405; cpu_ms 0.908× [0.791, 1.039], Δ -4.035; peak_rss_bytes 0.986× [0.965, 1.008], Δ -759808.000
- p5-user-add-existing: go-b/go-a — wall_ms 1.415× [1.285, 1.553], Δ +11.884; cpu_ms 1.192× [1.027, 1.379], Δ +7.282; peak_rss_bytes 1.000× [0.984, 1.016], Δ -6144.000
- p5-user-add-missing-role: go-b/go-a — wall_ms 1.042× [0.967, 1.123], Δ +1.192; cpu_ms 1.059× [1.019, 1.100], Δ +2.125; peak_rss_bytes 0.990× [0.982, 0.998], Δ -540672.000
- p5-user-add-cloud-rejected: go-b/go-a — wall_ms 1.002× [0.956, 1.051], Δ +0.055; cpu_ms 0.997× [0.916, 1.084], Δ -0.132; peak_rss_bytes 1.016× [1.006, 1.025], Δ +837632.000
- p5-user-set-role-missing-user: go-b/go-a — wall_ms 1.126× [1.041, 1.220], Δ +3.576; cpu_ms 1.067× [0.968, 1.167], Δ +2.557; peak_rss_bytes 1.010× [1.005, 1.014], Δ +520192.000
- p5-user-set-role-server-error: go-b/go-a — wall_ms 0.929× [0.776, 1.127], Δ -2.167; cpu_ms 0.908× [0.767, 1.084], Δ -3.579; peak_rss_bytes 0.997× [0.985, 1.010], Δ -143360.000
- p5-user-remove: go-b/go-a — wall_ms 0.987× [0.952, 1.023], Δ -0.397; cpu_ms 0.957× [0.943, 0.971], Δ -1.752; peak_rss_bytes 0.983× [0.968, 0.997], Δ -970752.000
- p5-user-remove-missing: go-b/go-a — wall_ms 1.016× [0.929, 1.116], Δ +0.472; cpu_ms 0.960× [0.840, 1.105], Δ -1.540; peak_rss_bytes 0.995× [0.989, 1.000], Δ -299008.000
- p5-user-remove-status-only-slow-body: go-b/go-a — wall_ms 0.849× [0.749, 0.966], Δ -5.005; cpu_ms 0.913× [0.787, 1.058], Δ -3.584; peak_rss_bytes 0.992× [0.984, 1.001], Δ -425984.000
- p5-role-remove-small: go-b/go-a — wall_ms 0.957× [0.889, 1.029], Δ -1.360; cpu_ms 0.985× [0.884, 1.092], Δ -0.589; peak_rss_bytes 0.983× [0.980, 0.987], Δ -942080.000
- p5-role-remove-large: go-b/go-a — wall_ms 0.931× [0.880, 0.988], Δ -2.381; cpu_ms 0.970× [0.953, 0.988], Δ -1.147; peak_rss_bytes 1.016× [1.008, 1.024], Δ +888832.000
- p5-role-remove-missing: go-b/go-a — wall_ms 0.810× [0.786, 0.834], Δ -8.097; cpu_ms 0.928× [0.821, 1.065], Δ -3.390; peak_rss_bytes 1.012× [0.999, 1.025], Δ +661504.000
- p5-role-remove-status-only-slow-body: go-b/go-a — wall_ms 1.031× [0.862, 1.218], Δ +0.966; cpu_ms 1.004× [0.894, 1.115], Δ +0.150; peak_rss_bytes 1.003× [0.991, 1.015], Δ +159744.000
- p5-refresh-user-list-small-json: go-b/go-a — wall_ms 1.076× [0.916, 1.265], Δ +2.680; cpu_ms 1.040× [0.880, 1.225], Δ +1.688; peak_rss_bytes 1.002× [0.995, 1.010], Δ +131072.000
- p5-refresh-user-list-large-json: go-b/go-a — wall_ms 1.013× [0.962, 1.067], Δ +7.241; cpu_ms 1.109× [1.057, 1.163], Δ +48.488; peak_rss_bytes 1.028× [0.989, 1.068], Δ +2854912.000
- p5-refresh-user-list-large-contention-json: go-b/go-a — wall_ms 1.004× [0.834, 1.202], Δ +2.882; cpu_ms 0.973× [0.847, 1.111], Δ -15.879; peak_rss_bytes 1.022× [0.996, 1.049], Δ +2498560.000
- p5-refresh-role-list-large-contention-json: go-b/go-a — wall_ms 1.076× [1.042, 1.111], Δ +17.157; cpu_ms 1.050× [1.015, 1.086], Δ +7.834; peak_rss_bytes 0.991× [0.985, 0.997], Δ -608256.000
- p5-refresh-dataset-add: go-b/go-a — wall_ms 0.850× [0.692, 1.094], Δ -6.736; cpu_ms 1.041× [0.892, 1.234], Δ +1.880; peak_rss_bytes 0.996× [0.996, 0.997], Δ -196608.000
- p5-refresh-set-role-replay: go-b/go-a — wall_ms 1.030× [0.999, 1.061], Δ +0.982; cpu_ms 1.000× [0.951, 1.051], Δ +0.019; peak_rss_bytes 1.003× [0.993, 1.014], Δ +180224.000
- p5-refresh-rejected-no-commit: go-b/go-a — wall_ms 0.977× [0.816, 1.175], Δ -0.828; cpu_ms 0.968× [0.794, 1.190], Δ -1.378; peak_rss_bytes 1.008× [0.996, 1.021], Δ +444416.000

## comparison (passed)

Purpose: measurement; 5 warmup and 30 measured rounds per binary.
Workloads with raw order/output variation between samples: 20; skipped (failures): none.

| Workload | Binary | Valid | Wall median / p95 ms | CV | CPU ms | Peak RSS MiB | First request ms | Request→stdout ms | Max in-flight | Requests | 401s (max) | Logical bytes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| p5-user-list-small-text | go | 30/30 | 31.901 / 38.806 | 0.101 | 40.095 | 53.275 | 27.103 | 3.205 | 3 | 9 | 0 | 2862 |
| p5-user-list-small-text | rust | 30/30 | 5.605 / 5.894 | 0.249 | 2.843 | 6.334 | 1.881 | 3.511 | 4 | 9 | 0 | 2862 |
| p5-role-list-small-text | go | 30/30 | 30.955 / 35.507 | 0.086 | 39.261 | 53.049 | 27.494 | 2.059 | 3 | 6 | 0 | 1216 |
| p5-role-list-small-text | rust | 30/30 | 4.241 / 4.700 | 0.093 | 2.322 | 6.199 | 1.731 | 2.238 | 3 | 6 | 0 | 1216 |
| p5-user-list-small-json | go | 30/30 | 31.981 / 37.800 | 0.092 | 40.023 | 53.146 | 27.255 | 3.290 | 3 | 9 | 0 | 3289 |
| p5-user-list-small-json | rust | 30/30 | 5.565 / 6.045 | 0.082 | 2.835 | 6.383 | 1.881 | 3.470 | 5 | 9 | 0 | 3289 |
| p5-role-list-small-json | go | 30/30 | 31.946 / 37.908 | 0.091 | 39.859 | 52.938 | 27.950 | 2.110 | 3 | 6 | 0 | 1382 |
| p5-role-list-small-json | rust | 30/30 | 4.078 / 4.931 | 0.104 | 2.298 | 6.293 | 1.679 | 2.227 | 3 | 6 | 0 | 1382 |
| p5-user-list-small-detail-failure-text | go | 30/30 | 32.644 / 35.343 | 0.078 | 40.448 | 53.266 | 27.657 | 3.302 | 3 | 9 | 0 | 2739 |
| p5-user-list-small-detail-failure-text | rust | 30/30 | 5.416 / 5.918 | 0.099 | 2.638 | 6.350 | 1.645 | 3.515 | 5 | 9 | 0 | 2739 |
| p5-user-list-small-detail-failure-json | go | 30/30 | 32.632 / 36.847 | 0.089 | 41.772 | 53.299 | 27.636 | N/A | 4 | 9 | 0 | 2326 |
| p5-user-list-small-detail-failure-json | rust | 30/30 | 5.470 / 5.925 | 0.064 | 2.741 | 6.400 | 1.809 | N/A | 4 | 9 | 0 | 2326 |
| p5-role-list-small-detail-failure-json | go | 30/30 | 30.694 / 34.119 | 0.074 | 38.562 | 52.895 | 27.451 | N/A | 3 | 6 | 0 | 895 |
| p5-role-list-small-detail-failure-json | rust | 30/30 | 4.154 / 4.665 | 0.107 | 2.413 | 6.240 | 1.728 | N/A | 4 | 6 | 0 | 895 |
| p5-user-list-large-text | go | 30/30 | 507.473 / 528.658 | 0.023 | 447.706 | 101.039 | 26.220 | 471.878 | 52 | 1001 | 0 | 3693576 |
| p5-user-list-large-text | rust | 30/30 | 500.597 / 521.911 | 0.035 | 282.773 | 35.961 | 1.887 | 497.184 | 553 | 1001 | 0 | 3693576 |
| p5-role-list-large-text | go | 30/30 | 151.540 / 162.888 | 0.050 | 148.014 | 63.508 | 26.185 | 99.419 | 31 | 251 | 0 | 579299 |
| p5-role-list-large-text | rust | 30/30 | 108.957 / 112.922 | 0.030 | 40.937 | 13.605 | 1.830 | 106.495 | 50 | 251 | 0 | 579299 |
| p5-user-list-large-json | go | 30/30 | 521.044 / 560.025 | 0.041 | 448.967 | 103.334 | 26.916 | 488.500 | 57 | 1001 | 0 | 3765908 |
| p5-user-list-large-json | rust | 30/30 | 512.285 / 538.752 | 0.042 | 282.108 | 35.832 | 1.911 | 508.299 | 336 | 1001 | 0 | 3765908 |
| p5-role-list-large-json | go | 30/30 | 133.681 / 144.147 | 0.040 | 128.077 | 65.000 | 27.760 | 103.528 | 25 | 251 | 0 | 776425 |
| p5-role-list-large-json | rust | 30/30 | 111.021 / 119.293 | 0.042 | 39.328 | 14.152 | 1.684 | 108.723 | 41 | 251 | 0 | 776425 |
| p5-user-list-large-skewed-json | go | 30/30 | 633.652 / 673.874 | 0.050 | 441.175 | 103.158 | 28.311 | 599.892 | 94 | 1001 | 0 | 3765908 |
| p5-user-list-large-skewed-json | rust | 30/30 | 600.549 / 623.366 | 0.019 | 277.793 | 36.031 | 1.896 | 597.051 | 152 | 1001 | 0 | 3765908 |
| p5-role-list-large-skewed-json | go | 30/30 | 243.010 / 259.050 | 0.084 | 127.740 | 65.135 | 26.600 | 214.477 | 48 | 251 | 0 | 776425 |
| p5-role-list-large-skewed-json | rust | 30/30 | 175.550 / 180.652 | 0.028 | 39.763 | 14.141 | 1.915 | 172.768 | 77 | 251 | 0 | 776425 |
| p5-user-list-large-detail-failure-text | go | 30/30 | 532.919 / 608.779 | 0.212 | 453.917 | 102.828 | 27.299 | 494.616 | 461 | 1001 | 0 | 3659595 |
| p5-user-list-large-detail-failure-text | rust | 30/30 | 518.344 / 560.831 | 0.035 | 293.230 | 35.908 | 1.960 | 514.998 | 127 | 1001 | 0 | 3659595 |
| p5-user-list-large-detail-failure-json | go | 30/30 | 521.141 / 541.572 | 0.028 | 443.558 | 103.203 | 27.482 | N/A | 66 | 1001 | 0 | 3599217 |
| p5-user-list-large-detail-failure-json | rust | 30/30 | 519.021 / 537.209 | 0.024 | 287.071 | 35.893 | 1.916 | N/A | 124 | 1001 | 0 | 3599217 |
| p5-role-list-large-detail-failure-json | go | 30/30 | 132.764 / 145.491 | 0.053 | 123.477 | 62.980 | 28.848 | N/A | 23 | 251 | 0 | 414998 |
| p5-role-list-large-detail-failure-json | rust | 30/30 | 112.738 / 122.561 | 0.046 | 41.362 | 13.576 | 1.905 | N/A | 52 | 251 | 0 | 414998 |
| p5-dataset-add-logs | go | 30/30 | 29.691 / 36.863 | 0.114 | 38.772 | 52.617 | 27.757 | 0.261 | 1 | 1 | 0 | 28 |
| p5-dataset-add-logs | rust | 30/30 | 2.257 / 2.472 | 0.113 | 1.727 | 6.215 | 1.790 | 0.266 | 1 | 1 | 0 | 28 |
| p5-dataset-add-metrics | go | 30/30 | 30.498 / 35.237 | 0.107 | 39.251 | 52.420 | 28.841 | 0.267 | 1 | 1 | 0 | 31 |
| p5-dataset-add-metrics | rust | 30/30 | 2.348 / 2.465 | 0.115 | 1.802 | 6.211 | 1.906 | 0.268 | 1 | 1 | 0 | 31 |
| p5-dataset-add-traces | go | 30/30 | 30.574 / 37.912 | 0.108 | 39.782 | 52.445 | 29.218 | 0.258 | 1 | 1 | 0 | 30 |
| p5-dataset-add-traces | rust | 30/30 | 2.220 / 2.711 | 0.138 | 1.670 | 6.158 | 1.726 | 0.294 | 1 | 1 | 0 | 30 |
| p5-dataset-add-status-only-slow-body | go | 30/30 | 29.553 / 36.998 | 0.115 | 39.347 | 52.449 | 27.778 | 0.264 | 1 | 1 | 0 | 32799 |
| p5-dataset-add-status-only-slow-body | rust | 30/30 | 2.262 / 3.469 | 0.283 | 1.724 | 6.127 | 1.853 | 0.257 | 1 | 1 | 0 | 32799 |
| p5-dataset-add-conflict | go | 30/30 | 33.178 / 38.028 | 0.105 | 42.864 | 52.541 | 31.592 | N/A | 1 | 1 | 0 | 90 |
| p5-dataset-add-conflict | rust | 30/30 | 2.249 / 2.751 | 0.166 | 1.691 | 6.148 | 1.763 | N/A | 1 | 1 | 0 | 90 |
| p5-dataset-add-non200-2xx | go | 30/30 | 29.477 / 39.260 | 0.134 | 39.938 | 52.473 | 28.808 | N/A | 1 | 1 | 0 | 58 |
| p5-dataset-add-non200-2xx | rust | 30/30 | 2.427 / 2.675 | 0.160 | 1.802 | 6.174 | 1.913 | N/A | 1 | 1 | 0 | 58 |
| p5-dataset-remove-guarded | go | 30/30 | 29.987 / 38.838 | 0.141 | 40.359 | 52.465 | 28.180 | 0.621 | 1 | 2 | 0 | 99 |
| p5-dataset-remove-guarded | rust | 30/30 | 2.736 / 3.098 | 0.137 | 1.992 | 6.234 | 1.879 | 0.658 | 1 | 2 | 0 | 99 |
| p5-dataset-remove-guarded-mismatch | go | 30/30 | 32.032 / 43.862 | 0.177 | 39.935 | 52.760 | 30.461 | N/A | 1 | 1 | 0 | 101 |
| p5-dataset-remove-guarded-mismatch | rust | 30/30 | 2.466 / 3.237 | 0.187 | 1.816 | 6.207 | 1.883 | N/A | 1 | 1 | 0 | 101 |
| p5-dataset-remove-unguarded | go | 30/30 | 29.851 / 39.210 | 0.161 | 39.309 | 53.385 | 28.211 | 0.236 | 1 | 1 | 0 | 44 |
| p5-dataset-remove-unguarded | rust | 30/30 | 2.249 / 2.531 | 0.173 | 1.677 | 6.215 | 1.772 | 0.265 | 1 | 1 | 0 | 44 |
| p5-dataset-remove-missing | go | 30/30 | 26.493 / 29.236 | 0.073 | 35.817 | 52.703 | 25.710 | N/A | 1 | 1 | 0 | 78 |
| p5-dataset-remove-missing | rust | 30/30 | 2.179 / 2.358 | 0.089 | 1.670 | 6.062 | 1.737 | N/A | 1 | 1 | 0 | 78 |
| p5-dataset-remove-status-only-slow-body | go | 30/30 | 31.503 / 41.156 | 0.136 | 39.208 | 52.619 | 30.337 | 0.263 | 1 | 1 | 0 | 32812 |
| p5-dataset-remove-status-only-slow-body | rust | 30/30 | 2.464 / 2.775 | 0.125 | 1.847 | 6.193 | 1.943 | 0.288 | 1 | 1 | 0 | 32812 |
| p5-user-add-small | go | 30/30 | 29.909 / 34.041 | 0.078 | 38.013 | 52.730 | 27.485 | 0.978 | 1 | 3 | 0 | 811 |
| p5-user-add-small | rust | 30/30 | 3.051 / 3.649 | 0.138 | 2.038 | 6.107 | 1.876 | 0.985 | 1 | 3 | 0 | 811 |
| p5-user-set-role-small | go | 30/30 | 32.606 / 42.062 | 0.187 | 39.983 | 52.766 | 29.986 | 1.012 | 1 | 3 | 0 | 760 |
| p5-user-set-role-small | rust | 30/30 | 3.097 / 3.507 | 0.126 | 2.054 | 6.086 | 1.947 | 1.000 | 1 | 3 | 0 | 760 |
| p5-user-add-large | go | 30/30 | 36.877 / 40.578 | 0.065 | 41.895 | 54.164 | 28.067 | 7.365 | 1 | 3 | 0 | 245694 |
| p5-user-add-large | rust | 30/30 | 11.344 / 14.318 | 0.148 | 6.277 | 10.070 | 1.876 | 9.022 | 1 | 3 | 0 | 245694 |
| p5-user-set-role-large | go | 30/30 | 38.036 / 43.743 | 0.091 | 44.057 | 53.820 | 29.406 | 7.330 | 1 | 3 | 0 | 245643 |
| p5-user-set-role-large | rust | 30/30 | 11.824 / 14.142 | 0.151 | 6.514 | 10.111 | 1.913 | 9.335 | 1 | 3 | 0 | 245643 |
| p5-user-add-duplicate-roles | go | 30/30 | 31.473 / 34.200 | 0.076 | 39.573 | 52.762 | 29.212 | 0.885 | 1 | 3 | 0 | 834 |
| p5-user-add-duplicate-roles | rust | 30/30 | 2.985 / 3.276 | 0.103 | 1.953 | 6.105 | 1.877 | 1.000 | 1 | 3 | 0 | 834 |
| p5-user-add-read-body-slow | go | 30/30 | 180.191 / 184.203 | 0.015 | 38.026 | 53.064 | 27.820 | 151.157 | 1 | 3 | 0 | 8953 |
| p5-user-add-read-body-slow | rust | 30/30 | 153.277 / 153.500 | 0.002 | 2.177 | 6.096 | 1.870 | 151.181 | 1 | 3 | 0 | 8953 |
| p5-user-add-existing | go | 30/30 | 29.794 / 36.366 | 0.141 | 37.981 | 52.576 | 27.783 | N/A | 1 | 1 | 0 | 618 |
| p5-user-add-existing | rust | 30/30 | 2.383 / 2.674 | 0.118 | 1.848 | 6.143 | 1.865 | N/A | 1 | 1 | 0 | 618 |
| p5-user-add-missing-role | go | 30/30 | 29.261 / 34.117 | 0.096 | 37.475 | 52.430 | 27.195 | N/A | 1 | 2 | 0 | 1098 |
| p5-user-add-missing-role | rust | 30/30 | 2.618 / 2.987 | 0.096 | 1.924 | 6.182 | 1.802 | N/A | 1 | 2 | 0 | 1098 |
| p5-user-add-cloud-rejected | go | 30/30 | 29.157 / 35.964 | 0.112 | 37.922 | 51.785 | N/A | N/A | 0 | 0 | 0 | 392 |
| p5-user-add-cloud-rejected | rust | 30/30 | 1.359 / 1.724 | 0.240 | 1.252 | 5.691 | N/A | N/A | 0 | 0 | 0 | 392 |
| p5-user-set-role-missing-user | go | 30/30 | 30.360 / 36.732 | 0.129 | 38.201 | 52.324 | 28.416 | N/A | 1 | 1 | 0 | 526 |
| p5-user-set-role-missing-user | rust | 30/30 | 2.223 / 2.692 | 0.145 | 1.592 | 6.143 | 1.637 | N/A | 1 | 1 | 0 | 526 |
| p5-user-set-role-server-error | go | 30/30 | 31.206 / 35.289 | 0.098 | 39.049 | 52.201 | 27.923 | N/A | 1 | 3 | 0 | 902 |
| p5-user-set-role-server-error | rust | 30/30 | 3.004 / 3.456 | 0.125 | 2.018 | 6.213 | 1.841 | N/A | 1 | 3 | 0 | 902 |
| p5-user-remove | go | 30/30 | 31.841 / 36.379 | 0.120 | 39.627 | 52.416 | 29.808 | 0.251 | 1 | 1 | 0 | 24 |
| p5-user-remove | rust | 30/30 | 2.320 / 2.603 | 0.123 | 1.693 | 6.092 | 1.844 | 0.268 | 1 | 1 | 0 | 24 |
| p5-user-remove-missing | go | 30/30 | 31.658 / 39.508 | 0.124 | 39.639 | 52.746 | 30.090 | N/A | 1 | 1 | 0 | 218 |
| p5-user-remove-missing | rust | 30/30 | 2.406 / 2.669 | 0.131 | 1.832 | 6.082 | 1.955 | N/A | 1 | 1 | 0 | 218 |
| p5-user-remove-status-only-slow-body | go | 30/30 | 33.677 / 38.807 | 0.112 | 41.380 | 52.559 | 31.849 | 0.268 | 1 | 1 | 0 | 32792 |
| p5-user-remove-status-only-slow-body | rust | 30/30 | 2.140 / 2.873 | 0.260 | 1.558 | 6.139 | 1.694 | 0.262 | 1 | 1 | 0 | 32792 |
| p5-role-remove-small | go | 30/30 | 31.453 / 38.216 | 0.117 | 39.697 | 52.576 | 29.513 | 0.574 | 1 | 2 | 0 | 399 |
| p5-role-remove-small | rust | 30/30 | 2.737 / 3.243 | 0.183 | 1.923 | 6.238 | 1.900 | 0.633 | 1 | 2 | 0 | 399 |
| p5-role-remove-large | go | 30/30 | 35.476 / 39.927 | 0.075 | 41.498 | 53.301 | 28.515 | 5.491 | 1 | 2 | 0 | 207586 |
| p5-role-remove-large | rust | 30/30 | 9.195 / 12.022 | 0.153 | 5.448 | 10.195 | 1.907 | 6.886 | 1 | 2 | 0 | 207586 |
| p5-role-remove-missing | go | 30/30 | 37.750 / 47.231 | 0.134 | 41.944 | 53.549 | 29.782 | N/A | 1 | 1 | 0 | 210530 |
| p5-role-remove-missing | rust | 30/30 | 8.587 / 12.371 | 0.254 | 5.210 | 10.188 | 1.765 | N/A | 1 | 1 | 0 | 210530 |
| p5-role-remove-status-only-slow-body | go | 30/30 | 28.046 / 41.206 | 0.184 | 37.793 | 57.342 | 26.640 | 0.635 | 1 | 2 | 0 | 33167 |
| p5-role-remove-status-only-slow-body | rust | 30/30 | 2.778 / 3.358 | 0.153 | 1.889 | 6.217 | 1.862 | 0.696 | 1 | 2 | 0 | 33167 |
| p5-refresh-user-list-small-json | go | 30/30 | 36.843 / 42.157 | 0.093 | 44.305 | 48.314 | 30.165 | 4.381 | 3 | 11 | 1 | 3464 |
| p5-refresh-user-list-small-json | rust | 30/30 | 6.358 / 6.963 | 0.070 | 3.082 | 6.400 | 1.884 | 4.296 | 4 | 11 | 1 | 3464 |
| p5-refresh-user-list-large-json | go | 30/30 | 568.992 / 619.339 | 0.062 | 466.872 | 97.371 | 30.604 | 532.398 | 462 | 1003 | 1 | 3766083 |
| p5-refresh-user-list-large-json | rust | 30/30 | 550.334 / 604.977 | 0.050 | 304.592 | 36.439 | 1.971 | 546.837 | 93 | 1003 | 1 | 3766083 |
| p5-refresh-user-list-large-contention-json | go | 30/30 | 813.536 / 1012.188 | 0.130 | 545.597 | 104.979 | 27.581 | 782.992 | 76 | 2002 | 1000 | 3787062 |
| p5-refresh-user-list-large-contention-json | rust | 30/30 | 937.614 / 1134.430 | 0.143 | 466.106 | 36.893 | 1.696 | 934.373 | 85 | 2002 | 1000 | 3787062 |
| p5-refresh-role-list-large-contention-json | go | 30/30 | 207.493 / 254.852 | 0.104 | 158.512 | 64.891 | 30.196 | 171.447 | 17 | 502 | 250 | 781829 |
| p5-refresh-role-list-large-contention-json | rust | 30/30 | 179.685 / 198.622 | 0.077 | 61.528 | 14.361 | 1.900 | 176.778 | 52 | 502 | 250 | 781829 |
| p5-refresh-dataset-add | go | 30/30 | 32.714 / 41.026 | 0.145 | 40.427 | 48.086 | 29.656 | 1.354 | 1 | 3 | 1 | 205 |
| p5-refresh-dataset-add | rust | 30/30 | 3.004 / 3.710 | 0.173 | 2.006 | 6.275 | 1.771 | 1.075 | 1 | 3 | 1 | 205 |
| p5-refresh-set-role-replay | go | 30/30 | 30.338 / 34.379 | 0.096 | 38.982 | 47.688 | 27.090 | 1.970 | 1 | 5 | 1 | 959 |
| p5-refresh-set-role-replay | rust | 30/30 | 3.587 / 4.256 | 0.145 | 2.223 | 6.156 | 1.731 | 1.744 | 1 | 5 | 1 | 959 |
| p5-refresh-rejected-no-commit | go | 30/30 | 29.805 / 35.476 | 0.118 | 39.041 | 47.850 | 26.925 | N/A | 1 | 2 | 1 | 413 |
| p5-refresh-rejected-no-commit | rust | 30/30 | 2.530 / 3.317 | 0.447 | 1.769 | 6.211 | 1.746 | N/A | 1 | 2 | 1 | 413 |

### Ratios (median, bootstrap 95% interval)

- p5-user-list-small-text: rust/go — wall_ms 0.176× [0.169, 0.184], Δ -26.295; cpu_ms 0.071× [0.067, 0.075], Δ -37.252; peak_rss_bytes 0.119× [0.117, 0.120], Δ -49221632.000
- p5-role-list-small-text: rust/go — wall_ms 0.137× [0.126, 0.145], Δ -26.714; cpu_ms 0.059× [0.056, 0.063], Δ -36.938; peak_rss_bytes 0.117× [0.115, 0.118], Δ -49125376.000
- p5-user-list-small-json: rust/go — wall_ms 0.174× [0.165, 0.181], Δ -26.417; cpu_ms 0.071× [0.067, 0.075], Δ -37.188; peak_rss_bytes 0.120× [0.118, 0.121], Δ -49035264.000
- p5-role-list-small-json: rust/go — wall_ms 0.128× [0.123, 0.133], Δ -27.868; cpu_ms 0.058× [0.054, 0.061], Δ -37.562; peak_rss_bytes 0.119× [0.117, 0.120], Δ -48910336.000
- p5-user-list-small-detail-failure-text: rust/go — wall_ms 0.166× [0.159, 0.176], Δ -27.228; cpu_ms 0.065× [0.061, 0.070], Δ -37.810; peak_rss_bytes 0.119× [0.118, 0.120], Δ -49195008.000
- p5-user-list-small-detail-failure-json: rust/go — wall_ms 0.168× [0.161, 0.179], Δ -27.162; cpu_ms 0.066× [0.063, 0.071], Δ -39.031; peak_rss_bytes 0.120× [0.118, 0.121], Δ -49176576.000
- p5-role-list-small-detail-failure-json: rust/go — wall_ms 0.135× [0.127, 0.145], Δ -26.540; cpu_ms 0.063× [0.056, 0.067], Δ -36.150; peak_rss_bytes 0.118× [0.117, 0.119], Δ -48920576.000
- p5-user-list-large-text: rust/go — wall_ms 0.986× [0.975, 0.997], Δ -6.876; cpu_ms 0.632× [0.622, 0.641], Δ -164.933; peak_rss_bytes 0.356× [0.353, 0.359], Δ -68239360.000
- p5-role-list-large-text: rust/go — wall_ms 0.719× [0.703, 0.739], Δ -42.583; cpu_ms 0.277× [0.271, 0.289], Δ -107.077; peak_rss_bytes 0.214× [0.214, 0.215], Δ -52326400.000
- p5-user-list-large-json: rust/go — wall_ms 0.983× [0.961, 0.997], Δ -8.759; cpu_ms 0.628× [0.615, 0.642], Δ -166.859; peak_rss_bytes 0.347× [0.344, 0.352], Δ -70780928.000
- p5-role-list-large-json: rust/go — wall_ms 0.830× [0.816, 0.847], Δ -22.661; cpu_ms 0.307× [0.302, 0.316], Δ -88.749; peak_rss_bytes 0.218× [0.217, 0.219], Δ -53317632.000
- p5-user-list-large-skewed-json: rust/go — wall_ms 0.948× [0.925, 0.976], Δ -33.103; cpu_ms 0.630× [0.612, 0.639], Δ -163.382; peak_rss_bytes 0.349× [0.346, 0.353], Δ -70387712.000
- p5-role-list-large-skewed-json: rust/go — wall_ms 0.722× [0.701, 0.740], Δ -67.460; cpu_ms 0.311× [0.301, 0.324], Δ -87.978; peak_rss_bytes 0.217× [0.216, 0.218], Δ -53471232.000
- p5-user-list-large-detail-failure-text: rust/go — wall_ms 0.973× [0.954, 0.989], Δ -14.575; cpu_ms 0.646× [0.625, 0.665], Δ -160.687; peak_rss_bytes 0.349× [0.345, 0.355], Δ -70170624.000
- p5-user-list-large-detail-failure-json: rust/go — wall_ms 0.996× [0.982, 1.013], Δ -2.121; cpu_ms 0.647× [0.630, 0.656], Δ -156.487; peak_rss_bytes 0.348× [0.345, 0.350], Δ -70580224.000
- p5-role-list-large-detail-failure-json: rust/go — wall_ms 0.849× [0.821, 0.875], Δ -20.026; cpu_ms 0.335× [0.314, 0.358], Δ -82.115; peak_rss_bytes 0.216× [0.215, 0.217], Δ -51804160.000
- p5-dataset-add-logs: rust/go — wall_ms 0.076× [0.068, 0.083], Δ -27.434; cpu_ms 0.045× [0.040, 0.048], Δ -37.045; peak_rss_bytes 0.118× [0.117, 0.119], Δ -48656384.000
- p5-dataset-add-metrics: rust/go — wall_ms 0.077× [0.068, 0.079], Δ -28.149; cpu_ms 0.046× [0.039, 0.048], Δ -37.449; peak_rss_bytes 0.118× [0.117, 0.119], Δ -48453632.000
- p5-dataset-add-traces: rust/go — wall_ms 0.073× [0.067, 0.077], Δ -28.355; cpu_ms 0.042× [0.037, 0.046], Δ -38.111; peak_rss_bytes 0.117× [0.116, 0.119], Δ -48535552.000
- p5-dataset-add-status-only-slow-body: rust/go — wall_ms 0.077× [0.074, 0.081], Δ -27.292; cpu_ms 0.044× [0.040, 0.046], Δ -37.623; peak_rss_bytes 0.117× [0.116, 0.118], Δ -48572416.000
- p5-dataset-add-conflict: rust/go — wall_ms 0.068× [0.058, 0.073], Δ -30.928; cpu_ms 0.039× [0.033, 0.044], Δ -41.173; peak_rss_bytes 0.117× [0.116, 0.118], Δ -48646144.000
- p5-dataset-add-non200-2xx: rust/go — wall_ms 0.082× [0.074, 0.085], Δ -27.050; cpu_ms 0.045× [0.042, 0.048], Δ -38.136; peak_rss_bytes 0.118× [0.116, 0.119], Δ -48547840.000
- p5-dataset-remove-guarded: rust/go — wall_ms 0.091× [0.083, 0.097], Δ -27.251; cpu_ms 0.049× [0.047, 0.054], Δ -38.368; peak_rss_bytes 0.119× [0.118, 0.120], Δ -48476160.000
- p5-dataset-remove-guarded-mismatch: rust/go — wall_ms 0.077× [0.067, 0.084], Δ -29.566; cpu_ms 0.045× [0.042, 0.050], Δ -38.120; peak_rss_bytes 0.118× [0.116, 0.119], Δ -48814080.000
- p5-dataset-remove-unguarded: rust/go — wall_ms 0.075× [0.068, 0.080], Δ -27.602; cpu_ms 0.043× [0.040, 0.047], Δ -37.633; peak_rss_bytes 0.116× [0.110, 0.118], Δ -49461248.000
- p5-dataset-remove-missing: rust/go — wall_ms 0.082× [0.077, 0.086], Δ -24.314; cpu_ms 0.047× [0.044, 0.048], Δ -34.147; peak_rss_bytes 0.115× [0.114, 0.118], Δ -48906240.000
- p5-dataset-remove-status-only-slow-body: rust/go — wall_ms 0.078× [0.072, 0.080], Δ -29.039; cpu_ms 0.047× [0.043, 0.049], Δ -37.361; peak_rss_bytes 0.118× [0.116, 0.119], Δ -48680960.000
- p5-user-add-small: rust/go — wall_ms 0.102× [0.093, 0.108], Δ -26.858; cpu_ms 0.054× [0.049, 0.055], Δ -35.975; peak_rss_bytes 0.116× [0.114, 0.117], Δ -48887808.000
- p5-user-set-role-small: rust/go — wall_ms 0.095× [0.086, 0.104], Δ -29.510; cpu_ms 0.051× [0.046, 0.055], Δ -37.929; peak_rss_bytes 0.115× [0.115, 0.116], Δ -48947200.000
- p5-user-add-large: rust/go — wall_ms 0.308× [0.283, 0.333], Δ -25.533; cpu_ms 0.150× [0.137, 0.162], Δ -35.618; peak_rss_bytes 0.186× [0.184, 0.188], Δ -46235648.000
- p5-user-set-role-large: rust/go — wall_ms 0.311× [0.276, 0.335], Δ -26.212; cpu_ms 0.148× [0.131, 0.159], Δ -37.544; peak_rss_bytes 0.188× [0.186, 0.189], Δ -45832192.000
- p5-user-add-duplicate-roles: rust/go — wall_ms 0.095× [0.087, 0.105], Δ -28.488; cpu_ms 0.049× [0.046, 0.053], Δ -37.620; peak_rss_bytes 0.116× [0.115, 0.117], Δ -48922624.000
- p5-user-add-read-body-slow: rust/go — wall_ms 0.851× [0.844, 0.854], Δ -26.914; cpu_ms 0.057× [0.054, 0.059], Δ -35.849; peak_rss_bytes 0.115× [0.114, 0.116], Δ -49250304.000
- p5-user-add-existing: rust/go — wall_ms 0.080× [0.075, 0.089], Δ -27.411; cpu_ms 0.049× [0.044, 0.052], Δ -36.134; peak_rss_bytes 0.117× [0.116, 0.118], Δ -48689152.000
- p5-user-add-missing-role: rust/go — wall_ms 0.089× [0.083, 0.095], Δ -26.643; cpu_ms 0.051× [0.047, 0.053], Δ -35.551; peak_rss_bytes 0.118× [0.116, 0.119], Δ -48494592.000
- p5-user-add-cloud-rejected: rust/go — wall_ms 0.047× [0.041, 0.053], Δ -27.798; cpu_ms 0.033× [0.029, 0.037], Δ -36.670; peak_rss_bytes 0.110× [0.109, 0.111], Δ -48332800.000
- p5-user-set-role-missing-user: rust/go — wall_ms 0.073× [0.067, 0.082], Δ -28.136; cpu_ms 0.042× [0.038, 0.049], Δ -36.609; peak_rss_bytes 0.117× [0.116, 0.119], Δ -48424960.000
- p5-user-set-role-server-error: rust/go — wall_ms 0.096× [0.089, 0.105], Δ -28.202; cpu_ms 0.052× [0.048, 0.056], Δ -37.030; peak_rss_bytes 0.119× [0.116, 0.120], Δ -48222208.000
- p5-user-remove: rust/go — wall_ms 0.073× [0.067, 0.079], Δ -29.520; cpu_ms 0.043× [0.038, 0.046], Δ -37.933; peak_rss_bytes 0.116× [0.114, 0.117], Δ -48574464.000
- p5-user-remove-missing: rust/go — wall_ms 0.076× [0.072, 0.082], Δ -29.252; cpu_ms 0.046× [0.042, 0.049], Δ -37.806; peak_rss_bytes 0.115× [0.115, 0.117], Δ -48930816.000
- p5-user-remove-status-only-slow-body: rust/go — wall_ms 0.064× [0.059, 0.070], Δ -31.537; cpu_ms 0.038× [0.034, 0.043], Δ -39.822; peak_rss_bytes 0.117× [0.116, 0.118], Δ -48674816.000
- p5-role-remove-small: rust/go — wall_ms 0.087× [0.077, 0.091], Δ -28.715; cpu_ms 0.048× [0.041, 0.052], Δ -37.774; peak_rss_bytes 0.119× [0.117, 0.120], Δ -48588800.000
- p5-role-remove-large: rust/go — wall_ms 0.259× [0.240, 0.292], Δ -26.281; cpu_ms 0.131× [0.124, 0.146], Δ -36.050; peak_rss_bytes 0.191× [0.189, 0.193], Δ -45199360.000
- p5-role-remove-missing: rust/go — wall_ms 0.227× [0.215, 0.271], Δ -29.163; cpu_ms 0.124× [0.118, 0.132], Δ -36.734; peak_rss_bytes 0.190× [0.189, 0.192], Δ -45467648.000
- p5-role-remove-status-only-slow-body: rust/go — wall_ms 0.099× [0.087, 0.110], Δ -25.269; cpu_ms 0.050× [0.045, 0.056], Δ -35.905; peak_rss_bytes 0.108× [0.106, 0.119], Δ -53608448.000
- p5-refresh-user-list-small-json: rust/go — wall_ms 0.173× [0.166, 0.188], Δ -30.485; cpu_ms 0.070× [0.066, 0.074], Δ -41.223; peak_rss_bytes 0.132× [0.131, 0.135], Δ -43950080.000
- p5-refresh-user-list-large-json: rust/go — wall_ms 0.967× [0.938, 0.994], Δ -18.658; cpu_ms 0.652× [0.634, 0.669], Δ -162.280; peak_rss_bytes 0.374× [0.367, 0.376], Δ -63891456.000
- p5-refresh-user-list-large-contention-json: rust/go — wall_ms 1.153× [1.031, 1.229], Δ +124.079; cpu_ms 0.854× [0.820, 0.903], Δ -79.491; peak_rss_bytes 0.351× [0.343, 0.363], Δ -71393280.000
- p5-refresh-role-list-large-contention-json: rust/go — wall_ms 0.866× [0.820, 0.915], Δ -27.808; cpu_ms 0.388× [0.367, 0.409], Δ -96.984; peak_rss_bytes 0.221× [0.211, 0.229], Δ -52983808.000
- p5-refresh-dataset-add: rust/go — wall_ms 0.092× [0.082, 0.102], Δ -29.710; cpu_ms 0.050× [0.043, 0.054], Δ -38.420; peak_rss_bytes 0.131× [0.128, 0.132], Δ -43841536.000
- p5-refresh-set-role-replay: rust/go — wall_ms 0.118× [0.110, 0.128], Δ -26.750; cpu_ms 0.057× [0.054, 0.061], Δ -36.759; peak_rss_bytes 0.129× [0.127, 0.131], Δ -43548672.000
- p5-refresh-rejected-no-commit: rust/go — wall_ms 0.085× [0.080, 0.094], Δ -27.275; cpu_ms 0.045× [0.042, 0.049], Δ -37.272; peak_rss_bytes 0.130× [0.128, 0.131], Δ -43661312.000

## Prior-Rust scope guard

45/45 workloads rejected with the deferred stub, no request and no state/config change.

## Unavailable or unmeasured quantities

- network_socket_bytes: N/A — loopback socket/TLS bytes are not captured; request/response bytes are logical HTTP bodies (response bytes are what the fixture wrote, not what the client read)
- heap_allocations: N/A — release runs are uninstrumented; use the diagnostic builds for allocation attribution
- fd_writes: N/A — requires strace; proc_io_syscw counts write-family calls only and excludes send/recv
- first_useful_result_ms: N/A — no command-specific useful-result predicate for administration output
- Per-workload N/A metrics are listed under `summaries.*.unavailable` in report.json.
- Block input/output counters are wait4 operations, not storage, socket or network bytes.

## Limitations

- The fixture is a Python ThreadingHTTPServer in a separate process; its scheduling and per-request cost are identical for every binary but add latency and noise relative to a production server.
- Control-mode reset reuses one fixture process per workload. The previous CLI has exited and every handler has finished before the model, ledger and fault budgets are rebuilt from the scenario. The fixture process is warm rather than fresh. `--fixture-reset process` restores per-sample fixture processes.
- Concurrent detail arrival order is scheduler dependent; only membership, multiplicity and the list-before-detail barrier (same-clock arrival after the list body write) are compared within a fan-out.
- Refresh contention: the number of rejected siblings is scheduler dependent; it is recorded (rejected_401_requests) and excluded from the oracle. Every causal constraint is enforced: one grant after the first 401; replay exactly once after each rejection; byte-identical replays; no rotated session before the grant; exact final membership.
- Request timestamps are taken after the fixture parses request headers; first_request_ms therefore includes client connect plus header transmission.
- Role removal targets an unassigned role; deletion of assigned roles is outside these workloads (phase5.py declares that server behavior separately).
- Expected list output and messages come from phase5.py's Go renderer model and canonicalizers; endpoint shapes follow phase5.py's Model. The fixture itself is a separate threaded server (phase5.py's AdminServer is a raw-socket scripted server).
- Measured max_in_flight is an observation of server arrivals, not a claim about the client task count. The default Rust inventory fan-out retains Go behavior after a 64-request cap failed the wide-barrier gate.
- The Go A/A stage is a correctness gate with its own small repetition count; its ratios are not a timing baseline.
- Loopback measurements establish no live-server performance claim; no real server is contacted or mutated.
