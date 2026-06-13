# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/calf-ai/calfkit-peripherals/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                    |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|------------------------------------------------------------------------ | -------: | -------: | -------: | -------: | ------: | --------: |
| src/calfkit\_tools/hermes/\_shims/agent/auxiliary\_client.py            |        2 |        1 |        0 |        0 |     50% |        11 |
| src/calfkit\_tools/hermes/\_shims/agent/lsp/range\_shift.py             |        3 |        3 |        0 |        0 |      0% |       2-6 |
| src/calfkit\_tools/hermes/\_shims/agent/lsp/reporter.py                 |        6 |        6 |        2 |        0 |      0% |      4-11 |
| src/calfkit\_tools/hermes/\_shims/agent/lsp/servers.py                  |        2 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/gateway/session\_context.py           |        7 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/gateway/status.py                     |       19 |       12 |        2 |        1 |     38% | 13, 18-28 |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/\_subprocess\_compat.py   |        7 |        2 |        2 |        1 |     67% |     15-17 |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/auth.py                   |        3 |        1 |        0 |        0 |     67% |        21 |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/config.py                 |       26 |        5 |        6 |        3 |     75% |34, 39-40, 57, 61 |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/nous\_account.py          |       53 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/plugins.py                |        3 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/hermes\_cli/profiles.py               |        2 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_shims/model\_tools.py                       |       30 |        8 |        6 |        0 |     72% |     46-53 |
| src/calfkit\_tools/hermes/\_vendor/agent/async\_utils.py                |       20 |       13 |        6 |        0 |     27% |     54-68 |
| src/calfkit\_tools/hermes/\_vendor/agent/file\_safety.py                |      202 |       74 |       72 |        9 |     65% |15-16, 24-25, 92-93, 121-122, 129-130, 135-136, 141-142, 223-224, 237, 261-262, 264, 277-278, 280, 289, 301, 348-361, 384-385, 395-419, 443, 491-494, 516-517, 524-527, 551, 600-606, 629 |
| src/calfkit\_tools/hermes/\_vendor/agent/i18n.py                        |      118 |       48 |       44 |        9 |     54% |108-111, 128-138, 148-162, 178-181, 187-191, 205-\>exit, 224-226, 236-238, 245, 248, 276, 281-282, 287-292 |
| src/calfkit\_tools/hermes/\_vendor/agent/redact.py                      |      134 |       71 |       54 |       13 |     45% |236-240, 246-248, 258-270, 279-286, 295, 303-307, 318-323, 349, 351, 355, 359, 364-367, 371-374, 380, 388-390, 395, 399, 403, 416, 420-425, 453, 484-485, 492, 495-496 |
| src/calfkit\_tools/hermes/\_vendor/agent/skill\_utils.py                |      319 |      280 |      160 |        1 |      8% |57-62, 73-82, 97-122, 148-169, 193-230, 255-269, 287-314, 318-322, 338, 359-424, 433-435, 443-450, 477-517, 529-556, 567-574, 587-612, 620-626, 638-644, 657-659, 664-666 |
| src/calfkit\_tools/hermes/\_vendor/agent/web\_search\_provider.py       |       22 |        6 |        0 |        0 |     73% |88, 101, 114, 123, 156, 180 |
| src/calfkit\_tools/hermes/\_vendor/agent/web\_search\_registry.py       |       83 |       24 |       30 |        4 |     65% |109-113, 169-171, 177-179, 185-194, 210-219, 238-239 |
| src/calfkit\_tools/hermes/\_vendor/hermes\_constants.py                 |      198 |      128 |       72 |        6 |     30% |27-28, 33, 41, 47-49, 71, 80-108, 131, 136, 157-165, 174-182, 193-201, 213-221, 258-262, 275-282, 307, 322-329, 338-339, 353-360, 374-391, 408, 414, 436-459 |
| src/calfkit\_tools/hermes/\_vendor/tools/ansi\_strip.py                 |        7 |        0 |        2 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/approval.py                    |      586 |      118 |      204 |       32 |     77% |61-64, 93, 103-105, 129-130, 608-612, 629, 631-632, 635-\>638, 646-647, 665, 673, 681, 690-691, 749, 751-753, 763-764, 794-796, 819-823, 856-857, 868-869, 876-878, 880-\>882, 898, 907-909, 922-923, 928-936, 976-983, 1017, 1021, 1025, 1033-1034, 1051-1087, 1111, 1113, 1118, 1150-\>exit, 1168-1171, 1180-1181, 1199-\>1192, 1259, 1270-1274, 1295, 1320-\>1324, 1332-1345, 1385, 1426-1447, 1572, 1653 |
| src/calfkit\_tools/hermes/\_vendor/tools/binary\_extensions.py          |        6 |        1 |        2 |        1 |     75% |        41 |
| src/calfkit\_tools/hermes/\_vendor/tools/budget\_config.py              |       20 |        0 |        4 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/code\_execution\_tool.py       |      671 |      348 |      206 |       40 |     46% |154-155, 202-204, 277, 492-493, 503, 508-511, 518-526, 530-537, 540-\>547, 559-561, 576-579, 581-\>exit, 584-585, 599-691, 702-704, 713-724, 743-866, 881-1059, 1088, 1094, 1115, 1150-1151, 1190-1193, 1253, 1261, 1269, 1311-1316, 1341-1348, 1374-1376, 1378-1380, 1386-1387, 1401-1406, 1441-1451, 1456, 1458-1462, 1466-1476, 1486-1489, 1497-1498, 1503-1548, 1566-1567, 1600-1604, 1623-1624, 1639, 1642-1643, 1651, 1652-\>1648, 1653-\>1652, 1656, 1661-1667, 1680, 1689, 1737-\>1739, 1739-\>1743, 1750, 1754, 1760 |
| src/calfkit\_tools/hermes/\_vendor/tools/credential\_files.py           |      205 |       47 |       90 |       11 |     72% |124, 126, 136, 145-171, 188-\>186, 193-195, 236-243, 269, 280, 281-\>274, 286-287, 323-335, 416-420 |
| src/calfkit\_tools/hermes/\_vendor/tools/env\_passthrough.py            |       60 |       12 |       18 |        1 |     76% |65-66, 115-137 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/base.py           |      375 |      135 |       88 |       23 |     60% |39, 48, 71-78, 87-93, 129-130, 144-154, 159-164, 169-170, 175-179, 236-237, 244-245, 340, 393-400, 410, 412, 414, 476-477, 535-551, 562, 566-567, 569-570, 575-590, 596-597, 601-602, 611-613, 621-623, 645, 658-666, 671-681, 694-706, 717-\>655, 719-739, 748-749, 752, 764-767, 793, 796-\>803, 805, 845-\>848, 853, 855, 861-862, 883, 888-889 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/daytona.py        |      141 |      121 |       28 |        0 |     12% |51-152, 156-158, 167-180, 184-196, 200, 208-211, 215-217, 223-242, 245-270 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/docker.py         |      547 |      476 |      214 |        7 |     10% |40-60, 68-90, 95-100, 117, 134-135, 173-\>176, 181-183, 185-189, 198-227, 238-261, 277, 282-284, 293-306, 360-364, 377-408, 419-424, 438-492, 530-901, 909-933, 939-956, 970, 980-1050, 1059-1067, 1077-1107, 1122-1164, 1208-1281, 1292-1296 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/file\_sync.py     |      225 |      182 |       80 |        0 |     14% |23-24, 60-76, 81, 86, 91, 96-100, 128-136, 147-211, 227-255, 262-280, 284-297, 301-375, 380-384, 396-403 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/local.py          |      310 |      110 |      128 |       17 |     63% |107-113, 118-124, 201-203, 210-211, 217, 225-\>221, 234, 250-285, 307-308, 314-315, 328, 338, 345-349, 367, 370-371, 409-410, 446-\>448, 479-480, 497, 511-\>513, 532-\>539, 558-\>564, 561-562, 572-637 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/managed\_modal.py |      141 |      102 |       36 |        0 |     22% |27-28, 55-70, 73-119, 122-146, 149, 152, 155-170, 173-212, 216-223, 233-240, 249-256, 260-265, 269-282 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/modal.py          |      277 |      236 |       74 |        0 |     12% |39, 43, 47, 51-59, 63-66, 70-80, 85-91, 99-121, 131-133, 136-138, 141-144, 147-155, 158-161, 183-293, 297-317, 332-367, 375-388, 392-398, 402, 412-440, 444-478 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/modal\_utils.py   |       99 |       60 |       18 |        0 |     33% |47-50, 55, 87-149, 153, 163-175, 183, 189, 192 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/singularity.py    |      164 |      139 |       46 |        0 |     12% |32-36, 45-60, 64, 68, 72-89, 93-101, 108-153, 175-193, 196-228, 234-244, 248-262 |
| src/calfkit\_tools/hermes/\_vendor/tools/environments/ssh.py            |      185 |      160 |       50 |        0 |     11% |26-31, 47-81, 84-98, 101-109, 113-125, 133-137, 143-156, 169-262, 268-274, 278-282, 286, 296-302, 305-319 |
| src/calfkit\_tools/hermes/\_vendor/tools/file\_operations.py            |      895 |      222 |      350 |       63 |     72% |62, 113, 215, 217, 221, 260, 354, 361, 365, 419-421, 538-539, 550-551, 561-563, 569-570, 577-582, 604-605, 743-\>746, 805, 812-830, 969, 974-975, 980, 984, 1000, 1012, 1026-1027, 1045-1090, 1105, 1109-1110, 1112, 1116, 1122, 1150, 1161-1193, 1207, 1307-\>1325, 1336-1337, 1354, 1393, 1411-1417, 1428-\>1432, 1434, 1514, 1550, 1554, 1569, 1591-1598, 1661, 1707, 1710-1711, 1726, 1729-1730, 1733-1734, 1754-1769, 1785-1786, 1789-1792, 1825-1826, 1829-1830, 1837-1858, 1900-1913, 1933, 1949, 1973-1975, 1980, 1985, 1996-1997, 2020, 2036-2042, 2064, 2080, 2084, 2086, 2124-2127, 2130-2139, 2157-2162, 2166-\>2150, 2168-\>2150, 2198, 2246-\>2245, 2248-\>2245, 2251-2252, 2276-\>2263, 2278-\>2263 |
| src/calfkit\_tools/hermes/\_vendor/tools/file\_state.py                 |      143 |       10 |       46 |        7 |     90% |103-\>108, 129-\>134, 168-170, 184-\>193, 193-\>209, 210, 239, 286-291 |
| src/calfkit\_tools/hermes/\_vendor/tools/file\_tools.py                 |      660 |       92 |      272 |       40 |     85% |56-57, 93-94, 102-\>105, 112-114, 191-192, 226-227, 254-258, 266-267, 277, 303-304, 314, 318-319, 322, 359-362, 369-370, 374, 378, 391, 433-437, 489-490, 498-499, 503-508, 516-517, 539, 588-589, 602-603, 607-\>677, 617, 619, 621, 643, 687, 713-714, 730, 789-790, 822-\>828, 845, 883-884, 925-\>exit, 926-\>928, 928-\>exit, 973-974, 1021-1022, 1056-\>1060, 1059, 1071-1072, 1075-1082, 1105-\>1109, 1110-\>1112, 1158-\>1154, 1161, 1171-1172, 1173-\>1168, 1193-1194, 1244-\>1241, 1262-\>1266, 1273, 1282-\>1287, 1288-1289, 1339-\>1343, 1341-\>1340, 1372-1373 |
| src/calfkit\_tools/hermes/\_vendor/tools/fuzzy\_match.py                |      334 |       33 |      164 |       26 |     87% |184, 201-\>200, 203, 233, 238, 267, 440, 466-469, 503-\>502, 511, 546, 549, 594, 621, 712, 727-731, 738-739, 743-744, 763, 769, 773, 793, 799-802, 809, 828, 837 |
| src/calfkit\_tools/hermes/\_vendor/tools/interrupt.py                   |       32 |       13 |        6 |        1 |     53% |32, 47-55, 85, 88, 91, 95 |
| src/calfkit\_tools/hermes/\_vendor/tools/lazy\_deps.py                  |      207 |       75 |       58 |        6 |     66% |281, 301-302, 307-308, 318-320, 324-326, 335-346, 355-403, 414, 448, 460-469, 479-\>482, 492-494, 568-569, 612-623 |
| src/calfkit\_tools/hermes/\_vendor/tools/managed\_tool\_gateway.py      |      109 |       80 |       36 |        0 |     20% |32, 36-49, 53-64, 68-72, 85-93, 98-121, 126-133, 138-148, 157-168, 188 |
| src/calfkit\_tools/hermes/\_vendor/tools/patch\_parser.py               |      315 |       66 |      180 |       30 |     76% |119-\>121, 131-\>133, 143-\>145, 157-159, 172-\>199, 192-197, 215, 219, 261-262, 272, 277, 300-301, 311, 314-322, 384, 392, 395-400, 402-\>374, 410-413, 432, 468-\>467, 475, 489, 493, 506-511, 527, 545-\>539, 558-582, 591, 593, 604, 611 |
| src/calfkit\_tools/hermes/\_vendor/tools/path\_security.py              |       15 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/process\_registry.py           |      782 |      142 |      258 |       43 |     80% |79-86, 297, 357, 410, 426, 479-480, 489-490, 497-498, 509-\>513, 511-512, 547, 580-583, 641-643, 646-647, 720, 722-725, 758-\>761, 764, 766-767, 772-773, 786-\>exit, 792-\>804, 799, 800-\>804, 809-\>786, 818-819, 824-829, 835-\>854, 838-\>835, 844, 848-851, 856-857, 878-880, 905-906, 911-\>902, 948-949, 959-\>979, 967-\>972, 969-970, 974-977, 980-\>984, 983, 1072-1073, 1079-1080, 1089, 1107, 1111-1118, 1122-1130, 1150-1152, 1157, 1165-1166, 1168-1171, 1174, 1177-1181, 1187, 1199-1200, 1206, 1208, 1216-1227, 1239, 1245-1246, 1249, 1253-1254, 1264-1267, 1294, 1330-1341, 1380-\>1379, 1404-1405, 1418-1419, 1425, 1602, 1605-1606 |
| src/calfkit\_tools/hermes/\_vendor/tools/registry.py                    |      258 |      154 |       78 |        4 |     34% |51-52, 77-78, 133-146, 152-153, 185, 189-195, 204, 208, 215-223, 227-228, 232-233, 267-294, 319-336, 353-389, 404, 407-408, 410-421, 429-435, 447-448, 452-453, 457-458, 462, 470-472, 476-478, 485-503, 507-524, 528-545, 578, 592-594 |
| src/calfkit\_tools/hermes/\_vendor/tools/terminal\_tool.py              |     1065 |      462 |      432 |       67 |     56% |125-149, 209-210, 216-220, 239, 282, 291, 307-318, 335-443, 447-454, 477-\>479, 482-\>491, 485-486, 493-494, 514, 519-525, 540-543, 583-584, 639, 809-811, 817, 892, 896, 899, 909-910, 918-919, 926-932, 1070-\>1072, 1075-1082, 1088-1091, 1214-1301, 1317-1319, 1330-1333, 1338, 1345-1366, 1375-1376, 1378-\>1371, 1398-1403, 1408-1410, 1424-1427, 1434-1456, 1483-1524, 1529-1549, 1708, 1845, 1847, 1849, 1851, 1908-1910, 1912-\>1978, 1914, 1919, 1929, 1946-\>1951, 1962-1963, 1979-\>2015, 1983-2000, 2008-2009, 2011-2012, 2018-2020, 2063, 2079, 2095-\>2138, 2138-\>2191, 2155-2183, 2192-2205, 2220-2221, 2225-2233, 2248-2249, 2252-2253, 2264-\>2305, 2275-2295, 2326-2330, 2336-2343, 2364, 2366, 2370-2374, 2393-2399, 2402-2406, 2415, 2435-2442, 2445, 2459, 2471-2475, 2478-2479, 2488-2490, 2495-2537 |
| src/calfkit\_tools/hermes/\_vendor/tools/thread\_context.py             |       41 |        7 |        6 |        2 |     81% |86-87, 91-\>104, 97-99, 112-113 |
| src/calfkit\_tools/hermes/\_vendor/tools/tirith\_security.py            |      439 |      371 |      152 |        2 |     12% |62-65, 79-80, 117-121, 129-130, 138, 143, 152-160, 171-177, 188-194, 202-206, 211-213, 223-241, 251, 256-261, 275-301, 306-326, 331-352, 363-450, 455, 476-563, 569-595, 607-685, 714-803, 812-820 |
| src/calfkit\_tools/hermes/\_vendor/tools/todo\_tool.py                  |       84 |        5 |       36 |        7 |     90% |58, 64-\>55, 66-\>55, 78-\>76, 136, 140, 144, 202 |
| src/calfkit\_tools/hermes/\_vendor/tools/tool\_backend\_helpers.py      |       77 |       35 |       18 |        3 |     52% |30-41, 62-64, 73-74, 82, 94-95, 118-\>120, 143, 154-161, 172-182 |
| src/calfkit\_tools/hermes/\_vendor/tools/tool\_output\_limits.py        |       35 |        0 |        6 |        0 |    100% |           |
| src/calfkit\_tools/hermes/\_vendor/tools/url\_safety.py                 |      133 |        7 |       46 |        0 |     96% |246-247, 261-265, 317-318 |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/brave\_free.py  |       47 |       20 |        2 |        1 |     57% |    73-123 |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/ddgs.py         |       43 |        6 |        4 |        1 |     85% |50-51, 78, 88-90 |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/searxng.py      |       46 |       20 |        2 |        1 |     56% |    63-126 |
| src/calfkit\_tools/hermes/\_vendor/tools/web\_providers/tavily.py       |       77 |       10 |       14 |        3 |     86% |114-115, 155, 170-172, 184, 201-203 |
| src/calfkit\_tools/hermes/\_vendor/utils.py                             |      166 |      109 |       38 |        3 |     32% |28, 40-41, 57-58, 124, 138-141, 144-151, 176-203, 218-267, 280-283, 291-297, 302, 321-326, 331-335, 351-355, 370-376 |
| src/calfkit\_tools/hermes/node/\_runtime.py                             |       38 |        1 |        8 |        1 |     96% |        60 |
| src/calfkit\_tools/hermes/node/code.py                                  |       19 |        0 |        4 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/files.py                                 |       17 |        0 |        0 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/shell.py                                 |       18 |        0 |        4 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/todo.py                                  |       38 |        0 |        2 |        0 |    100% |           |
| src/calfkit\_tools/hermes/node/web.py                                   |       56 |        5 |       16 |        1 |     92% |59-60, 74-75, 127 |
| src/calfkit\_tools/web\_fetch/\_vendor/\_ssrf.py                        |      207 |        3 |       70 |        0 |     99% |38, 370-371 |
| src/calfkit\_tools/web\_fetch/\_vendor/common\_tools/web\_fetch.py      |       72 |        0 |       12 |        0 |    100% |           |
| src/calfkit\_tools/web\_fetch/results.py                                |       10 |        0 |        0 |        0 |    100% |           |
| **TOTAL**                                                               | **11756** | **4877** | **4064** |  **491** | **56%** |           |


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