# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/calf-ai/calfkit-peripherals/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                   |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|----------------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| src/calfkit\_tools/hermes/\_shims/agent/auxiliary\_client.py           |        2 |        1 |        0 |        0 |     50% |        11 |
| src/calfkit\_tools/hermes/\_shims/agent/lsp/servers.py                 |        2 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/gateway/session\_context.py          |        7 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/gateway/status.py                    |       19 |        0 |        2 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/\_subprocess\_compat.py  |        7 |        2 |        2 |        1 |     67% |     15-17 |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/auth.py                  |        3 |        1 |        0 |        0 |     67% |        21 |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/config.py                |       26 |        3 |        6 |        1 |     88% |34, 57, 61 |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/nous\_account.py         |       53 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/plugins.py               |        3 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/profiles.py              |        2 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/model\_tools.py                      |       30 |        8 |        6 |        0 |     72% |     46-53 |
| src/calfkit\_tools/hermes/\_vendor/agent/file\_safety.py               |      202 |       29 |       72 |        5 |     88% |15-16, 24-25, 92-93, 121-122, 129-130, 135-136, 141-142, 223-224, 261-262, 277-278, 351-352, 357-\>361, 384-385, 397, 493-\>487, 516-517, 551, 629 |
| src/calfkit\_tools/hermes/\_vendor/agent/i18n.py                       |      118 |        8 |       44 |        1 |     91% |   128-138 |
| src/calfkit\_tools/hermes/\_vendor/agent/redact.py                     |      134 |       24 |       54 |        4 |     85% |259, 263-264, 279-286, 295, 303-307, 322, 453, 484-485, 492, 495-496 |
| src/calfkit\_tools/hermes/\_vendor/agent/web\_search\_provider.py      |       22 |        6 |        0 |        0 |     73% |88, 101, 114, 123, 156, 180 |
| src/calfkit\_tools/hermes/\_vendor/agent/web\_search\_registry.py      |       83 |       24 |       30 |        4 |     65% |109-113, 169-171, 177-179, 185-194, 210-219, 238-239 |
| src/calfkit\_tools/hermes/\_vendor/hermes\_constants.py                |      198 |      101 |       72 |        1 |     44% |47-49, 105-106, 157-165, 174-182, 193-201, 213-221, 275-282, 322-329, 338-339, 353-360, 374-391, 408, 414, 436-459 |
| src/calfkit\_tools/hermes/\_vendor/tools/ansi\_strip.py                |        7 |        0 |        2 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/approval.py                   |      586 |       72 |      204 |       20 |     87% |61-64, 93, 103-105, 129-130, 631-632, 635-\>638, 681, 749, 751-753, 763-764, 819-823, 856-857, 876-878, 880-\>882, 898, 907-909, 922-923, 928-936, 976-983, 1017, 1021, 1025, 1033-1034, 1051-1087, 1150-\>exit, 1180-1181, 1199-\>1192, 1270-\>1284, 1295, 1428, 1430-1432, 1572, 1653 |
| src/calfkit\_tools/hermes/\_vendor/tools/binary\_extensions.py         |        6 |        1 |        2 |        1 |     75% |        41 |
| src/calfkit\_tools/hermes/\_vendor/tools/budget\_config.py             |       20 |        0 |        4 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/code\_execution\_tool.py      |      671 |      136 |      206 |       21 |     79% |154-155, 203, 277, 492-493, 503, 584-585, 599-691, 719-724, 758-759, 770, 861-863, 866, 1001-1002, 1115, 1150-1151, 1190-1193, 1253, 1269, 1311-\>1314, 1315-1316, 1347-1348, 1386-1387, 1448, 1461-\>1464, 1466-1476, 1486-1489, 1497-1498, 1508-1511, 1514-1523, 1529-1548, 1566-1567, 1642-1643, 1661-1665, 1689 |
| src/calfkit\_tools/hermes/\_vendor/tools/credential\_files.py          |      205 |       26 |       90 |       10 |     84% |148-\>147, 164-\>147, 170-171, 188-\>186, 193-195, 236-243, 269, 280, 281-\>274, 286-287, 323-335, 419-420 |
| src/calfkit\_tools/hermes/\_vendor/tools/env\_passthrough.py           |       60 |       12 |       18 |        1 |     76% |65-66, 115-137 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/base.py          |      375 |       74 |       88 |       19 |     78% |39, 74-\>exit, 129-130, 144-154, 159-164, 169-170, 175-179, 236-237, 244-245, 340, 538, 540, 543-544, 549-551, 562, 566-567, 575-590, 596-597, 601-602, 621-623, 645, 659, 672, 694-706, 728, 737-738, 752, 805, 845-\>848, 853, 855, 861-862, 883 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/local.py         |      310 |       54 |      128 |        8 |     81% |107-113, 118-124, 202-203, 250-285, 307-308, 367, 370-371, 479-480, 511-\>513, 532-\>539, 558-\>564, 561-562, 579-581, 590-591, 597-598, 603, 626-627, 631-632, 636-637 |
| src/calfkit\_tools/hermes/\_vendor/tools/file\_operations.py           |      895 |      124 |      350 |       48 |     84% |62, 113, 215, 217, 221, 260, 354, 361, 365, 419-421, 538-539, 550-551, 569-570, 604-605, 743-\>746, 827-\>832, 974-975, 980, 1026-1027, 1056-\>1087, 1059, 1065, 1071, 1074, 1077, 1081-\>1084, 1109-1110, 1207, 1336-1337, 1354, 1393, 1415-1416, 1428-\>1432, 1434, 1550, 1569, 1591-1598, 1661, 1707, 1710-1711, 1726, 1729-1730, 1733-1734, 1754-1769, 1785-1786, 1789-1792, 1825-1826, 1829-1830, 1837-1858, 1900-1913, 1933, 1980, 1996-1997, 2080, 2084, 2124-2127, 2132-\>2131, 2134-\>2131, 2166-\>2150, 2168-\>2150, 2246-\>2245, 2248-\>2245, 2276-\>2263, 2278-\>2263 |
| src/calfkit\_tools/hermes/\_vendor/tools/file\_state.py                |      143 |       10 |       46 |        7 |     90% |103-\>108, 129-\>134, 168-170, 184-\>193, 193-\>209, 210, 239, 286-291 |
| src/calfkit\_tools/hermes/\_vendor/tools/file\_tools.py                |      660 |       52 |      272 |       27 |     92% |56-57, 93-94, 102-\>105, 112-114, 191-192, 257-258, 303-304, 314, 318-319, 322, 359-362, 378, 436-437, 489-490, 498-499, 507-508, 516-517, 588-589, 602-603, 607-\>677, 617, 619, 621, 643, 687, 713-714, 730, 822-\>828, 845, 926-\>928, 928-\>exit, 1080, 1105-\>1109, 1110-\>1112, 1171-1172, 1173-\>1168, 1193-1194, 1244-\>1241, 1262-\>1266, 1288-1289, 1339-\>1343, 1341-\>1340, 1372-1373 |
| src/calfkit\_tools/hermes/\_vendor/tools/fuzzy\_match.py               |      334 |       33 |      164 |       26 |     87% |184, 201-\>200, 203, 233, 238, 267, 440, 466-469, 503-\>502, 511, 546, 549, 594, 621, 712, 727-731, 738-739, 743-744, 763, 769, 773, 793, 799-802, 809, 828, 837 |
| src/calfkit\_tools/hermes/\_vendor/tools/interrupt.py                  |       32 |        6 |        6 |        2 |     79% |32, 55, 85, 88, 91, 95 |
| src/calfkit\_tools/hermes/\_vendor/tools/lazy\_deps.py                 |      207 |       75 |       58 |        6 |     66% |281, 301-302, 307-308, 318-320, 324-326, 335-346, 355-403, 414, 448, 460-469, 479-\>482, 492-494, 568-569, 612-623 |
| src/calfkit\_tools/hermes/\_vendor/tools/patch\_parser.py              |      315 |       39 |      180 |       25 |     85% |119-\>121, 131-\>133, 143-\>145, 157-159, 172-\>199, 192-197, 215, 219, 300-301, 315-316, 392, 395-400, 402-\>374, 410-413, 468-\>467, 489, 493, 506-511, 527, 545-\>539, 558-\>575, 561-\>575, 571-\>575, 576-582, 591, 604, 611 |
| src/calfkit\_tools/hermes/\_vendor/tools/path\_security.py             |       15 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/process\_registry.py          |      782 |      112 |      258 |       37 |     85% |297, 357, 410, 426, 479-480, 489-490, 497-498, 547, 580-583, 641-643, 646-647, 720, 722-725, 758-\>761, 764, 766-767, 772-773, 786-\>exit, 792-\>804, 799, 800-\>804, 809-\>786, 818-819, 824-829, 838-\>835, 844, 848-851, 856-857, 878-880, 905-906, 911-\>902, 948-949, 959-\>979, 967-\>972, 969-970, 974-977, 980-\>984, 983, 1072-1073, 1079-1080, 1089, 1107, 1111-1118, 1122-1130, 1150-1152, 1157, 1165-1166, 1168-1171, 1177-1181, 1199-1200, 1216-1217, 1226-1227, 1239, 1245-1246, 1249, 1253-1254, 1266-1267, 1294, 1339-\>1337, 1380-\>1379, 1404-1405, 1418-1419, 1425, 1602, 1605-1606 |
| src/calfkit\_tools/hermes/\_vendor/tools/registry.py                   |      258 |       71 |       78 |       12 |     66% |51-52, 77-78, 137-139, 152-153, 185, 215-223, 227-228, 232-233, 267-294, 322, 328-\>335, 362, 367-\>369, 378-383, 407-408, 419-420, 429-435, 447-448, 452-453, 462, 489-\>498, 500-502, 507-524, 535, 578, 592-594 |
| src/calfkit\_tools/hermes/\_vendor/tools/terminal\_tool.py             |     1065 |      389 |      432 |       53 |     63% |125-149, 209-210, 216-220, 239, 282, 291, 307-318, 335-443, 448, 451-454, 477-\>479, 482-\>491, 493-494, 521-522, 583-584, 639, 809-811, 817, 892, 896, 899, 909-910, 918-919, 926-932, 1070-\>1072, 1075-1082, 1088-1091, 1214-1301, 1317-1319, 1330-1333, 1338, 1345-1366, 1375-1376, 1402-1403, 1434-1456, 1483-1524, 1529-1549, 1708, 1845, 1847, 1849, 1851, 1908-1910, 1912-\>1978, 1914, 1919, 1929, 1946-\>1951, 1962-1963, 2063, 2079, 2138-\>2191, 2155-2183, 2195-2205, 2232-2233, 2264-\>2305, 2370-2374, 2393-2399, 2402-2406, 2415, 2435-2442, 2445, 2459, 2471-2475, 2478-2479, 2488-2490, 2495-2537 |
| src/calfkit\_tools/hermes/\_vendor/tools/thread\_context.py            |       41 |        7 |        6 |        2 |     81% |86-87, 91-\>104, 97-99, 112-113 |
| src/calfkit\_tools/hermes/\_vendor/tools/tirith\_security.py           |      439 |       42 |      152 |        7 |     90% |62-65, 79-80, 193-194, 256-261, 306-326, 332-\>331, 334, 340-341, 367-369, 386-388, 420, 677-\>685 |
| src/calfkit\_tools/hermes/\_vendor/tools/todo\_tool.py                 |       84 |        5 |       36 |        7 |     90% |58, 64-\>55, 66-\>55, 78-\>76, 136, 140, 144, 202 |
| src/calfkit\_tools/hermes/\_vendor/tools/tool\_backend\_helpers.py     |       77 |        5 |       18 |        1 |     94% |62-64, 94-95 |
| src/calfkit\_tools/hermes/\_vendor/tools/tool\_output\_limits.py       |       35 |        0 |        6 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/url\_safety.py                |      133 |        7 |       46 |        0 |     96% |246-247, 261-265, 317-318 |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/brave\_free.py |       47 |        0 |        2 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/ddgs.py        |       43 |        3 |        4 |        1 |     91% | 50-51, 78 |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/searxng.py     |       46 |        0 |        2 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/tavily.py      |       77 |        0 |       14 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/utils.py                            |      166 |       93 |       38 |        0 |     42% |176-203, 218-267, 280-283, 291-297, 302, 321-326, 331-335, 351-355, 370-376 |
| src/calfkit\_tools/hermes/node/\_runtime.py                            |       38 |        1 |        8 |        1 |     96% |        60 |
| src/calfkit\_tools/hermes/node/code.py                                 |       19 |        0 |        4 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/files.py                                |       17 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/shell.py                                |       18 |        0 |        4 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/todo.py                                 |       38 |        0 |        2 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/web.py                                  |       56 |        5 |       16 |        1 |     92% |59-60, 74-75, 127 |
| src/calfkit\_tools/tools.py                                            |       18 |        0 |        4 |        0 |    100% |           |
| src/calfkit\_tools/web\_fetch/\_vendor/\_ssrf.py                       |      207 |        3 |       70 |        0 |     99% |38, 370-371 |
| src/calfkit\_tools/web\_fetch/\_vendor/common\_tools/web\_fetch.py     |       72 |        0 |       12 |        0 |    100% |           |
| src/calfkit\_tools/web\_fetch/results.py                               |       10 |        0 |        0 |        0 |    100% |           |
| **TOTAL**                                                              | **9538** | **1664** | **3318** |  **360** | **81%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/calf-ai/calfkit-peripherals/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/calf-ai/calfkit-peripherals/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/calf-ai/calfkit-peripherals/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/calf-ai/calfkit-peripherals/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fcalf-ai%2Fcalfkit-peripherals%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/calf-ai/calfkit-peripherals/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.