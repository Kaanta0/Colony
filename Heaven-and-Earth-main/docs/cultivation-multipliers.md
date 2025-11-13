# Cultivation Multipliers

The table below lists the default innate-stat multipliers applied to each cultivation realm and phase. Values are generated from the progressive curve in `bot/game.py` where:

- Qi Condensation begins at a 1.00× multiplier.
- Each phase within a realm increases the multiplier by a realm-dependent step: it starts at 0.10× during Qi Condensation and grows by 12 % plus 0.01× per breakthrough, so higher realms spread their phases further apart (values are rounded to two decimals).
- Realm transitions add an increment that starts at 1.30× and, after every breakthrough, grows by 18 % plus an additional 0.40× so each higher realm widens the gap dramatically.

| Realm | Initial | Early | Mid | Late | Peak |
| --- | --- | --- | --- | --- | --- |
| Qi Condensation | 1.00× | 1.10× | 1.20× | 1.30× | 1.40× |
| Foundation Establishment | 2.30× | 2.42× | 2.54× | 2.67× | 2.79× |
| Core Formation | 4.23× | 4.38× | 4.52× | 4.67× | 4.82× |
| Nascent Soul | 6.92× | 7.09× | 7.27× | 7.44× | 7.62× |
| Soul Formation | 10.48× | 10.69× | 10.89× | 11.10× | 11.30× |
| Soul Transformation | 15.09× | 15.33× | 15.57× | 15.81× | 16.05× |
| Ascendant | 20.92× | 21.20× | 21.48× | 21.76× | 22.03× |
| Illusory Yin | 28.21× | 28.53× | 28.85× | 29.18× | 29.50× |
| Corporeal Yang | 37.21× | 37.58× | 37.95× | 38.32× | 38.69× |
| Nirvana Scryer | 48.22× | 48.65× | 49.07× | 49.50× | 49.92× |
| Nirvana Cleanser | 61.63× | 62.12× | 62.60× | 63.09× | 63.57× |
| Nirvana Shatterer | 77.84× | 78.39× | 78.95× | 79.50× | 80.06× |
| Heaven's Blight | 97.37× | 98.00× | 98.63× | 99.26× | 99.89× |
| Nirvana Void | 120.81× | 121.53× | 122.24× | 122.96× | 123.68× |
| Spirit Void | 148.88× | 149.69× | 150.51× | 151.32× | 152.13× |
| Arcane Void | 182.40× | 183.32× | 184.24× | 185.16× | 186.08× |
| Void Tribulant | 222.35× | 223.39× | 224.43× | 225.47× | 226.51× |
| Half-Heaven Trampling | 269.90× | 271.08× | 272.25× | 273.43× | 274.60× |
| Heaven Trampling | 326.40× | 327.73× | 329.05× | 330.38× | 331.71× |

Multiply the appropriate row by your innate stat roll to see where your character lands. For example, a Strength roll of 14 at the
Nirvana Scryer mid phase now results in `14 × 49.07 = 686.98` base Strength before equipment and other bonuses.

## Sample stat progression

The table below applies the curve to a character with the following innate talents:

- Strength: 14 (Average)
- Physique: 11 (Average)
- Agility: 15 (Genius)

| Realm | Phase | Strength | Physique | Agility | Multiplier |
| --- | --- | --- | --- | --- | --- |
| Qi Condensation | Initial | 14.00 | 11.00 | 15.00 | 1.00× |
|  | Early | 15.40 | 12.10 | 16.50 | 1.10× |
|  | Mid | 16.80 | 13.20 | 18.00 | 1.20× |
|  | Late | 18.20 | 14.30 | 19.50 | 1.30× |
|  | Peak | 19.60 | 15.40 | 21.00 | 1.40× |
| Foundation Establishment | Initial | 32.20 | 25.30 | 34.50 | 2.30× |
|  | Early | 33.91 | 26.64 | 36.33 | 2.42× |
|  | Mid | 35.62 | 27.98 | 38.16 | 2.54× |
|  | Late | 37.32 | 29.33 | 39.99 | 2.67× |
|  | Peak | 39.03 | 30.67 | 41.82 | 2.79× |
| Core Formation | Initial | 59.22 | 46.53 | 63.45 | 4.23× |
|  | Early | 61.27 | 48.14 | 65.65 | 4.38× |
|  | Mid | 63.32 | 49.76 | 67.85 | 4.52× |
|  | Late | 65.38 | 51.37 | 70.05 | 4.67× |
|  | Peak | 67.43 | 52.98 | 72.25 | 4.82× |
| Nascent Soul | Initial | 96.88 | 76.12 | 103.80 | 6.92× |
|  | Early | 99.32 | 78.04 | 106.41 | 7.09× |
|  | Mid | 101.76 | 79.95 | 109.03 | 7.27× |
|  | Late | 104.20 | 81.87 | 111.64 | 7.44× |
|  | Peak | 106.64 | 83.78 | 114.25 | 7.62× |
| Soul Formation | Initial | 146.72 | 115.28 | 157.20 | 10.48× |
|  | Early | 149.59 | 117.54 | 160.28 | 10.69× |
|  | Mid | 152.46 | 119.79 | 163.35 | 10.89× |
|  | Late | 155.33 | 122.05 | 166.43 | 11.10× |
|  | Peak | 158.21 | 124.30 | 169.51 | 11.30× |
| Soul Transformation | Initial | 211.26 | 165.99 | 226.35 | 15.09× |
|  | Early | 214.62 | 168.63 | 229.95 | 15.33× |
|  | Mid | 217.97 | 171.27 | 233.54 | 15.57× |
|  | Late | 221.33 | 173.90 | 237.14 | 15.81× |
|  | Peak | 224.69 | 176.54 | 240.74 | 16.05× |
| Ascendant | Initial | 292.88 | 230.12 | 313.80 | 20.92× |
|  | Early | 296.78 | 233.18 | 317.98 | 21.20× |
|  | Mid | 300.68 | 236.25 | 322.16 | 21.48× |
|  | Late | 304.58 | 239.31 | 326.33 | 21.76× |
|  | Peak | 308.48 | 242.37 | 330.51 | 22.03× |
| Illusory Yin | Initial | 394.94 | 310.31 | 423.15 | 28.21× |
|  | Early | 399.45 | 313.85 | 427.98 | 28.53× |
|  | Mid | 403.96 | 317.39 | 432.81 | 28.85× |
|  | Late | 408.46 | 320.94 | 437.64 | 29.18× |
|  | Peak | 412.97 | 324.48 | 442.47 | 29.50× |
| Corporeal Yang | Initial | 520.94 | 409.31 | 558.15 | 37.21× |
|  | Early | 526.13 | 413.39 | 563.71 | 37.58× |
|  | Mid | 531.32 | 417.46 | 569.27 | 37.95× |
|  | Late | 536.51 | 421.54 | 574.83 | 38.32× |
|  | Peak | 541.69 | 425.62 | 580.39 | 38.69× |
| Nirvana Scryer | Initial | 675.08 | 530.42 | 723.30 | 48.22× |
|  | Early | 681.03 | 535.10 | 729.68 | 48.65× |
|  | Mid | 686.98 | 539.77 | 736.05 | 49.07× |
|  | Late | 692.93 | 544.45 | 742.43 | 49.50× |
|  | Peak | 698.89 | 549.12 | 748.81 | 49.92× |
| Nirvana Cleanser | Initial | 862.82 | 677.93 | 924.45 | 61.63× |
|  | Early | 869.63 | 683.28 | 931.74 | 62.12× |
|  | Mid | 876.43 | 688.62 | 939.03 | 62.60× |
|  | Late | 883.24 | 693.97 | 946.32 | 63.09× |
|  | Peak | 890.04 | 699.32 | 953.62 | 63.57× |
| Nirvana Shatterer | Initial | 1089.76 | 856.24 | 1167.60 | 77.84× |
|  | Early | 1097.52 | 862.34 | 1175.92 | 78.39× |
|  | Mid | 1105.28 | 868.44 | 1184.23 | 78.95× |
|  | Late | 1113.04 | 874.54 | 1192.55 | 79.50× |
|  | Peak | 1120.81 | 880.63 | 1200.86 | 80.06× |
| Heaven's Blight | Initial | 1363.18 | 1071.07 | 1460.55 | 97.37× |
|  | Early | 1372.01 | 1078.01 | 1470.01 | 98.00× |
|  | Mid | 1380.85 | 1084.95 | 1479.48 | 98.63× |
|  | Late | 1389.68 | 1091.89 | 1488.94 | 99.26× |
|  | Peak | 1398.51 | 1098.83 | 1498.40 | 99.89× |
| Nirvana Void | Initial | 1691.34 | 1328.91 | 1812.15 | 120.81× |
|  | Early | 1701.37 | 1336.79 | 1822.90 | 121.53× |
|  | Mid | 1711.40 | 1344.68 | 1833.65 | 122.24× |
|  | Late | 1721.44 | 1352.56 | 1844.40 | 122.96× |
|  | Peak | 1731.47 | 1360.44 | 1855.15 | 123.68× |
| Spirit Void | Initial | 2084.32 | 1637.68 | 2233.20 | 148.88× |
|  | Early | 2095.70 | 1646.62 | 2245.39 | 149.69× |
|  | Mid | 2107.07 | 1655.56 | 2257.58 | 150.51× |
|  | Late | 2118.45 | 1664.50 | 2269.77 | 151.32× |
|  | Peak | 2129.83 | 1673.43 | 2281.96 | 152.13× |
| Arcane Void | Initial | 2553.60 | 2006.40 | 2736.00 | 182.40× |
|  | Early | 2566.48 | 2016.52 | 2749.80 | 183.32× |
|  | Mid | 2579.37 | 2026.64 | 2763.61 | 184.24× |
|  | Late | 2592.25 | 2036.77 | 2777.41 | 185.16× |
|  | Peak | 2605.13 | 2046.89 | 2791.21 | 186.08× |
| Void Tribulant | Initial | 3112.90 | 2445.85 | 3335.25 | 222.35× |
|  | Early | 3127.47 | 2457.30 | 3350.86 | 223.39× |
|  | Mid | 3142.04 | 2468.74 | 3366.47 | 224.43× |
|  | Late | 3156.61 | 2480.19 | 3382.08 | 225.47× |
|  | Peak | 3171.17 | 2491.64 | 3397.69 | 226.51× |
| Half-Heaven Trampling | Initial | 3778.60 | 2968.90 | 4048.50 | 269.90× |
|  | Early | 3795.06 | 2981.83 | 4066.13 | 271.08× |
|  | Mid | 3811.51 | 2994.76 | 4083.76 | 272.25× |
|  | Late | 3827.97 | 3007.69 | 4101.39 | 273.43× |
|  | Peak | 3844.42 | 3020.62 | 4119.02 | 274.60× |
| Heaven Trampling | Initial | 4569.60 | 3590.40 | 4896.00 | 326.40× |
|  | Early | 4588.17 | 3604.99 | 4915.90 | 327.73× |
|  | Mid | 4606.74 | 3619.58 | 4935.80 | 329.05× |
|  | Late | 4625.31 | 3634.17 | 4955.69 | 330.38× |
|  | Peak | 4643.88 | 3648.77 | 4975.59 | 331.71× |

These sample numbers can be used as a reference point—swap in your own talent rolls to project the innate stats you should see at
each phase.

These values ensure cultivators within the same realm stay relatively competitive, while any breakthrough to a new realm delivers a significant leap in power.
