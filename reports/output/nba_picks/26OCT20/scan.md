# NBA 26OCT20 read-only scan
Pulled UTC: 2026-10-20 17:42:17


## 26OCT20BOSDET: Boston vs Detroit
event times: {'expected_expiration_time': '2026-10-21T01:00:00Z', 'occurrence_datetime': '2026-10-20T22:00:00Z'}

Win: Boston bid 0.52 (73974) ask 0.53 (574047) mid 0.525; Detroit bid 0.47 (75465) ask 0.48 (1299320) mid 0.475

Spread-implied median margin: 1.6 (positive = Boston wins by that many (interpolated across valid spread mids))


Spread ladder (strike: bid/ask/mid)

- BOS: 1.5:0.5/0.51/0.505, 2.5:0.45/0.46/0.455, 3.5:0.42/0.43/0.425, 4.5:0.4/0.41/0.405, 5.5:0.38/0.39/0.385, 6.5:0.34/0.35/0.345, 8.5:0.29/0.3/0.295, 10.5:0.23/0.24/0.235, 12.5:0.18/0.19/0.185, 14.5:0.15/0.16/0.155, 16.5:0.12/0.13/0.125, 18.5:0.09/0.1/0.095, 20.5:0.08/0.09/0.085, 22.5:0.05/0.06/0.055, 24.5:0.03/0.05/0.04, 26.5:0.03/0.05/0.04
- DET: 1.5:0.44/0.45/0.445, 2.5:0.41/0.42/0.415, 3.5:0.39/0.4/0.395, 4.5:0.35/0.37/0.36, 5.5:0.32/0.33/0.325, 6.5:0.29/0.3/0.295, 7.5:0.26/0.27/0.265, 9.5:0.21/0.22/0.215, 11.5:0.17/0.18/0.175, 13.5:0.13/0.15/0.14, 15.5:0.1/0.12/0.11, 17.5:0.08/0.1/0.09, 19.5:0.06/0.07/0.065, 21.5:0.04/0.06/0.05, 23.5:0.03/0.04/0.035, 25.5:0.02/0.03/0.025, 27.5:0.01/0.03/0.02

| team | bucket | bid (sz) | ask (sz) | fair | EV yes | EV no | flag |
|---|---|---|---|---|---|---|---|
| BOS | 1-5 | 0.18 (5) | 0.2 (1683) | 0.140 | -0.071 | 0.030 | interp fair 0.140 (EVy -, EVn -) | BUY NO |
| BOS | 6-10 | 0.13 (25) | 0.16 (48) | 0.150 | -0.019 | -0.028 | interp fair 0.150 (EVy -, EVn -) |  |
| BOS | 11-20 | 0.11 (348) | 0.17 (1) | 0.150 | -0.030 | -0.047 | interp fair 0.150 (EVy -, EVn -) |  |
| BOS | 21+ | 0.05 (28) | 0.09 (17) | 0.085 | -0.011 | -0.038 | interp fair 0.085 (EVy -, EVn -) |  |
| DET | 1-5 | 0.12 (43) | 0.16 (18) | 0.150 | -0.019 | -0.037 | interp fair 0.150 (EVy -, EVn -) |  |
| DET | 6-10 | 0.09 (10) | 0.15 (525) | - | - | - | interp fair 0.130 (EVy -0.029, EVn -0.046) | invalid/missing input leg (P(by>10.5)) |
| DET | 11-20 | 0.08 (469) | 0.15 (13) | - | - | - | interp fair 0.138 (EVy -0.021, EVn -0.063) | invalid/missing input leg (P(by>10.5)) |
| DET | 21+ | 0.04 (2001) | 0.07 (1) | - | - | - | interp fair 0.058 (EVy -0.017, EVn -0.020) | invalid/missing input leg (P(by>20.5)) |

Fair sum (5/8 strict buckets): None; with interpolated fill-in: 1.0

Other series (market counts): {'KXNBA1H': 3, 'KXNBA1HSPREAD': 19, 'KXNBA1HTEAMTOTAL': 22, 'KXNBA1HTOTAL': 19, 'KXNBA1Q': 3, 'KXNBA1QSPREAD': 13, 'KXNBA1QTOTAL': 13, 'KXNBA2D': 4, 'KXNBA2H': 3, 'KXNBA2HSPREAD': 19, 'KXNBA2HTOTAL': 19, 'KXNBA2Q': 3, 'KXNBA2QSPREAD': 13, 'KXNBA2QTOTAL': 13, 'KXNBA3D': 2, 'KXNBA3PT': 36, 'KXNBA3Q': 3, 'KXNBA3QSPREAD': 13, 'KXNBA3QTOTAL': 13, 'KXNBA4Q': 3, 'KXNBA4QSPREAD': 13, 'KXNBA4QTOTAL': 13, 'KXNBAAST': 29, 'KXNBABLK': 13, 'KXNBAGAME': 2, 'KXNBAOT': 1, 'KXNBAPRA': 19, 'KXNBAPTS': 40, 'KXNBARACE': 0, 'KXNBAREB': 48, 'KXNBASPREAD': 33, 'KXNBASTL': 13, 'KXNBASTOCK': 21, 'KXNBATEAMTOTAL': 38, 'KXNBATOTAL': 29, 'KXNBAWINMARGIN': 8}


## 26OCT20OKCSAS: Oklahoma City vs San Antonio
event times: {'expected_expiration_time': '2026-10-21T07:30:00Z', 'occurrence_datetime': '2026-10-21T04:30:00Z'}

Win: Oklahoma City bid 0.49 (96601) ask 0.5 (48134) mid 0.495; San Antonio bid 0.5 (13267) ask 0.51 (465100) mid 0.505

Spread-implied median margin: -0.7 (positive = Oklahoma City wins by that many (interpolated across valid spread mids))


Spread ladder (strike: bid/ask/mid)

- OKC: 1.5:0.46/0.48/0.47, 2.5:0.42/0.44/0.43, 3.5:0.39/0.41/0.4, 4.5:0.36/0.38/0.37, 5.5:0.34/0.35/0.345, 7.5:0.29/0.3/0.295, 9.5:0.24/0.25/0.245, 11.5:0.18/0.21/0.195, 13.5:0.14/0.16/0.15, 15.5:0.12/0.13/0.125, 17.5:0.08/0.11/0.095, 19.5:0.06/0.08/0.07, 21.5:0.06/0.07/0.065, 23.5:0.04/0.05/0.045, 25.5:0.02/0.04/0.03
- SAS: 1.5:0.47/0.49/0.48, 2.5:0.44/0.45/0.445, 3.5:0.4/0.42/0.41, 4.5:0.37/0.38/0.375, 5.5:0.34/0.36/0.35, 6.5:0.32/0.34/0.33, 7.5:0.29/0.31/0.3, 8.5:0.27/0.28/0.275, 10.5:0.22/0.23/0.225, 12.5:0.17/0.19/0.18, 14.5:0.14/0.16/0.15, 16.5:0.11/0.13/0.12, 18.5:0.08/0.1/0.09, 20.5:0.07/0.08/0.075, 22.5:0.04/0.07/0.055, 24.5:0.03/0.05/0.04, 26.5:0.02/0.03/0.025, 28.5:0.01/0.03/0.02

| team | bucket | bid (sz) | ask (sz) | fair | EV yes | EV no | flag |
|---|---|---|---|---|---|---|---|
| OKC | 1-5 | 0.11 (20) | 0.16 (152) | 0.150 | -0.019 | -0.047 | interp fair 0.150 (EVy -, EVn -) |  |
| OKC | 6-10 | 0.11 (6) | 0.18 (132) | - | - | - | interp fair 0.125 (EVy -0.065, EVn -0.022) | invalid/missing input leg (P(by>10.5)) |
| OKC | 11-20 | 0.02 (12014) | 0.12 (2) | - | - | - | interp fair 0.152 (EVy 0.025, EVn -0.134) | invalid/missing input leg (P(by>10.5)) |
| OKC | 21+ | 0.01 (1000) | 0.07 (1) | - | - | - | interp fair 0.068 (EVy -0.007, EVn -0.058) | invalid/missing input leg (P(by>20.5)) |
| SAS | 1-5 | 0.08 (21) | 0.16 (91) | 0.155 | -0.014 | -0.080 | interp fair 0.155 (EVy -, EVn -) |  |
| SAS | 6-10 | 0.08 (20) | 0.15 (1) | 0.125 | -0.034 | -0.050 | interp fair 0.125 (EVy -, EVn -) |  |
| SAS | 11-20 | 0.05 (20) | 0.16 (13) | 0.150 | -0.019 | -0.103 | interp fair 0.150 (EVy -, EVn -) |  |
| SAS | 21+ | 0.02 (108) | 0.07 (23) | 0.075 | 0.000 | -0.056 | interp fair 0.075 (EVy -, EVn -) |  |

Fair sum (5/8 strict buckets): None; with interpolated fill-in: 1.0

Other series (market counts): {'KXNBA1H': 3, 'KXNBA1HSPREAD': 19, 'KXNBA1HTEAMTOTAL': 22, 'KXNBA1HTOTAL': 19, 'KXNBA1Q': 3, 'KXNBA1QSPREAD': 13, 'KXNBA1QTOTAL': 13, 'KXNBA2D': 5, 'KXNBA2H': 3, 'KXNBA2HSPREAD': 19, 'KXNBA2HTOTAL': 19, 'KXNBA2Q': 3, 'KXNBA2QSPREAD': 13, 'KXNBA2QTOTAL': 13, 'KXNBA3D': 2, 'KXNBA3PT': 33, 'KXNBA3Q': 3, 'KXNBA3QSPREAD': 13, 'KXNBA3QTOTAL': 13, 'KXNBA4Q': 3, 'KXNBA4QSPREAD': 13, 'KXNBA4QTOTAL': 13, 'KXNBAAST': 31, 'KXNBABLK': 6, 'KXNBAGAME': 2, 'KXNBAOT': 1, 'KXNBAPRA': 18, 'KXNBAPTS': 41, 'KXNBARACE': 0, 'KXNBAREB': 51, 'KXNBASPREAD': 33, 'KXNBASTL': 12, 'KXNBATEAMTOTAL': 38, 'KXNBATOTAL': 29, 'KXNBAWINMARGIN': 8}


## 26OCT20PHINYK: Philadelphia vs New York
event times: {'expected_expiration_time': '2026-10-21T05:00:00Z', 'occurrence_datetime': '2026-10-21T02:00:00Z'}

Win: Philadelphia bid 0.38 (34202) ask 0.39 (373854) mid 0.385; New York bid 0.61 (52592) ask 0.62 (177495) mid 0.615

Spread-implied median margin: -4.6 (positive = Philadelphia wins by that many (interpolated across valid spread mids))


Spread ladder (strike: bid/ask/mid)

- PHI: 1.5:0.35/0.36/0.355, 2.5:0.33/0.34/0.335, 4.5:0.27/0.28/0.275, 6.5:0.22/0.23/0.225, 8.5:0.18/0.19/0.185, 10.5:0.14/0.16/0.15, 12.5:0.1/0.12/0.11, 14.5:0.08/0.1/0.09, 16.5:0.06/0.08/0.07, 18.5:0.05/0.06/0.055, 20.5:0.03/0.04/0.035, 22.5:0.02/0.03/0.025
- NYK: 1.5:0.59/0.61/0.6, 2.5:0.57/0.58/0.575, 3.5:0.53/0.55/0.54, 4.5:0.5/0.51/0.505, 5.5:0.45/0.46/0.455, 6.5:0.42/0.43/0.425, 7.5:0.4/0.41/0.405, 8.5:0.36/0.38/0.37, 9.5:0.33/0.35/0.34, 10.5:0.31/0.32/0.315, 11.5:0.29/0.3/0.295, 13.5:0.23/0.25/0.24, 15.5:0.18/0.2/0.19, 17.5:0.16/0.17/0.165, 19.5:0.12/0.14/0.13, 21.5:0.09/0.11/0.1, 23.5:0.07/0.09/0.08, 25.5:0.06/0.07/0.065, 27.5:0.04/0.05/0.045, 29.5:0.03/0.04/0.035, 31.5:0.02/0.03/0.025

| team | bucket | bid (sz) | ask (sz) | fair | EV yes | EV no | flag |
|---|---|---|---|---|---|---|---|
| PHI | 1-5 | 0.08 (20) | 0.14 (104) | - | - | - | interp fair 0.135 (EVy -0.013, EVn -0.060) | invalid/missing input leg (P(by>5.5)) |
| PHI | 6-10 | 0.08 (54) | 0.11 (34) | - | - | - | interp fair 0.100 (EVy -0.017, EVn -0.025) | invalid/missing input leg (P(by>5.5)) |
| PHI | 11-20 | 0.02 (113) | 0.1 (11) | 0.115 | 0.009 | -0.096 | interp fair 0.115 (EVy -, EVn -) |  |
| PHI | 21+ | 0.01 (1001) | 0.05 (2) | 0.035 | -0.018 | -0.026 | interp fair 0.035 (EVy -, EVn -) |  |
| NYK | 1-5 | 0.11 (30) | 0.17 (6) | 0.160 | -0.020 | -0.057 | interp fair 0.160 (EVy -, EVn -) |  |
| NYK | 6-10 | 0.12 (10) | 0.17 (13) | 0.140 | -0.040 | -0.027 | interp fair 0.140 (EVy -, EVn -) |  |
| NYK | 11-20 | 0.11 (21) | 0.21 (10) | - | - | - | interp fair 0.200 (EVy -0.022, EVn -0.097) | invalid/missing input leg (P(by>20.5)) |
| NYK | 21+ | 0.08 (111) | 0.12 (20) | - | - | - | interp fair 0.115 (EVy -0.012, EVn -0.040) | invalid/missing input leg (P(by>20.5)) |

Fair sum (4/8 strict buckets): None; with interpolated fill-in: 1.0

Other series (market counts): {'KXNBA1H': 3, 'KXNBA1HSPREAD': 19, 'KXNBA1HTEAMTOTAL': 22, 'KXNBA1HTOTAL': 19, 'KXNBA1Q': 3, 'KXNBA1QSPREAD': 13, 'KXNBA1QTOTAL': 13, 'KXNBA2D': 6, 'KXNBA2H': 3, 'KXNBA2HSPREAD': 19, 'KXNBA2HTOTAL': 19, 'KXNBA2Q': 3, 'KXNBA2QSPREAD': 13, 'KXNBA2QTOTAL': 13, 'KXNBA3D': 3, 'KXNBA3PT': 53, 'KXNBA3Q': 3, 'KXNBA3QSPREAD': 13, 'KXNBA3QTOTAL': 13, 'KXNBA4Q': 3, 'KXNBA4QSPREAD': 13, 'KXNBA4QTOTAL': 13, 'KXNBAAST': 39, 'KXNBABLK': 9, 'KXNBAGAME': 2, 'KXNBAOT': 1, 'KXNBAPRA': 28, 'KXNBAPTS': 40, 'KXNBARACE': 0, 'KXNBAREB': 43, 'KXNBASPREAD': 33, 'KXNBASTL': 23, 'KXNBATEAMTOTAL': 38, 'KXNBATOTAL': 29, 'KXNBAWINMARGIN': 8}
