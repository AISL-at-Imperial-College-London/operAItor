"""The plant's P&ID, described once and drawn twice:

    frontend/public/static/pid.svg   the console's P&ID view and docs/pid.md (live: data-sif / data-pv hooks)
    docs/pid/pid.drawio              the editable version (draw.io / diagrams.net; its P&ID symbol library)

    python docs/pid/build_pid.py

Conventions (ISA 5.1): process lines solid; instrument signals dashed; SIS signals red dash-dot. Controllers are
DCS bubbles (circle with a line), switches field bubbles, interlocks diamonds I1 ... I11 (the cause & effect
matrix's numbers). Control valves have a diaphragm actuator; SIS shutdown valves are solenoid valves, black when
normally closed and white when normally open, labelled with the interlocks that act on them. tests/test_pid_drawing.py
checks that both files are up to date and that every tag of the tables is drawn.
"""
from __future__ import annotations

import html
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from operaitor.plant.cause_effect import CAUSES, INTERLOCK, matrix  # noqa: E402

W, H = 1700, 1140
DY = 34                      # everything below the title
INK, GREY, RED, STEAM, CW = "#111", "#555", "#c62828", "#b23a3a", "#1f6fd1"

ITEMS: list[tuple] = []      # (kind, data) in drawing order


def line(points, style="proc", arrow=False):
    ITEMS.append(("line", {"pts": points, "style": style, "arrow": arrow}))


def text(x, y, s, size=12, bold=False, color=INK, anchor="start", attrs=None, top=False):
    ITEMS.append(("text", {"x": x, "y": y, "s": s, "size": size, "bold": bold, "color": color, "anchor": anchor,
                           "attrs": attrs or {}, "top": top}))


def box(x, y, w, h, r=0, stroke=INK, width=2.2, fill="#fff"):
    ITEMS.append(("box", {"x": x, "y": y, "w": w, "h": h, "r": r, "stroke": stroke, "width": width, "fill": fill}))


def symbol(shape, x, y, **kw):
    ITEMS.append((shape, {"x": x, "y": y, **kw}))


def group(attrs):
    ITEMS.append(("group", attrs))


def end_group():
    ITEMS.append(("end_group", {}))


LINE_STYLES = {  # svg stroke, width, dash; draw.io equivalents are derived
    "proc": (INK, 2, None), "thick": (INK, 3, None), "sig": (INK, 1, "5 4"), "sis": (RED, 1.2, "8 3 2 3"),
    "steam": (STEAM, 2, None), "cw": (CW, 2, None), "tray": ("#666", 1.2, "6 3"), "thin": (INK, 1.4, None),
}

# ---------------------------------------------------------------------------------------------- the drawing
def build() -> None:
    ITEMS.clear()
    rows = {r["sif"]: r for r in matrix()}
    # ------------------------------------------------------------------ feed
    text(24, 300, "Fresh IPA feed")
    text(24, 314, "298 K, 100 kmol/h", 11, color=GREY)
    line([(30, 330), (114, 330)], arrow=True)
    symbol("pump", 130, 330)
    text(104, 378, "P-101A/B", bold=True)
    line([(146, 316), (180, 316), (180, 325), (232, 325)])
    symbol("valve", 245, 325, kind="control")
    text(226, 352, "FV-101", bold=True)
    line([(258, 325), (317, 325)])
    symbol("valve", 330, 325, kind="sol_no")
    text(313, 352, "XV-101", bold=True)
    line([(343, 325), (380, 325)], arrow=True)
    symbol("inst", 245, 250, letters="FIC", num="101", kind="dcs")
    text(245, 285, "throughput", 10, color="#777", anchor="middle")
    line([(245, 270), (245, 302)], "sig")

    # ------------------------------------------------------------------ reactor
    box(380, 200, 140, 310, r=40)
    line([(370, 330), (370, 475), (378, 500), (400, 515), (450, 522), (500, 515), (522, 500), (530, 475), (530, 330)],
         "steam")
    line([(400, 420)] + [(400 + 12 * i, 406 if i % 2 else 420) for i in range(1, 9)], "cw")
    text(450, 455, "E-102 coil", 11, color=CW, anchor="middle")
    line([(488, 168), (488, 385)])
    line([(472, 385), (504, 385)])
    box(476, 150, 24, 18, width=1.6)
    text(472, 164, "M-101", 11, anchor="end")
    text(432, 290, "R-101", 16, bold=True, anchor="middle")
    text(432, 308, "CSTR, 6 m³", 11, color="#444", anchor="middle")
    text(432, 323, "IPA → acetone", 10.5, color="#444", anchor="middle")
    text(432, 337, "+ H₂, endothermic", 10.5, color="#444", anchor="middle")

    # jacket heating: XV-103 (SIS) and TV-101 (R_TIC)
    text(110, 458, "Heating medium", color=STEAM)
    text(110, 472, "390 K", color=STEAM)
    line([(110, 480), (250, 480)], "steam")
    symbol("valve", 262, 480, kind="sol_no")
    text(236, 505, "XV-103", bold=True)
    line([(274, 480), (296, 480)], "steam")
    symbol("valve", 309, 480, kind="control")
    text(294, 505, "TV-101", bold=True)
    line([(322, 480), (370, 480)], "steam", arrow=True)
    symbol("inst", 309, 410, letters="TIC", num="101", kind="dcs")
    text(309, 446, "R_TIC", 10, color="#777", anchor="middle")
    line([(309, 430), (309, 457)], "sig")
    line([(329, 410), (372, 410)], "sig")
    text(335, 402, "TT-101", 10, color=GREY)

    # emergency cooling: XV-102, normally closed
    text(200, 550, "Cooling water", color=CW)
    line([(220, 560), (296, 560)], "cw")
    symbol("valve", 309, 560, kind="sol_nc")
    text(290, 585, "XV-102", bold=True)
    text(290, 598, "emergency cooling", 10, color=GREY)
    line([(322, 560), (392, 560), (392, 430)], "cw", arrow=True)

    # H2 vent (PCV-101, R_PIC), blowdown (BDV-101) and relief (PSV-101) to the flare header
    line([(510, 212), (510, 110), (610, 110)])
    line([(636, 110), (780, 110)])
    line([(555, 110), (555, 160), (610, 160)])
    line([(636, 160), (780, 160)])
    line([(530, 110), (530, 70), (611, 70)])
    line([(635, 70), (780, 70)])
    line([(780, 60), (780, 170)], "thick")
    line([(780, 115), (840, 115)], arrow=True)
    text(800, 104, "Flare /")
    text(800, 140, "fuel-gas header")
    symbol("valve", 623, 110, kind="control")
    text(640, 128, "PCV-101", bold=True)
    symbol("valve", 623, 160, kind="sol_nc")
    text(604, 185, "BDV-101", bold=True)
    text(604, 198, "blowdown", 10, color=GREY)
    symbol("psv", 623, 70)
    text(604, 50, "PSV-101", bold=True)
    text(664, 50, "opens 4.50 → full 4.70 bar", 10, color=GREY)
    text(503, 124, "H₂ 70 kmol/h, 3.0 bar", 11, color=GREY, anchor="end")
    symbol("inst", 700, 232, letters="PIC", num="101", kind="dcs")
    text(700, 268, "R_PIC", 10, color="#777", anchor="middle")
    line([(700, 212), (700, 91), (633, 91)], "sig")          # to PCV-101's actuator
    line([(680, 232), (520, 232)], "sig")
    text(528, 226, "PT-101", 10, color=GREY)

    # reactor outlet → column feed
    line([(450, 512), (450, 640), (500, 640)])
    symbol("pump", 516, 640)
    text(488, 688, "P-102A/B", bold=True)
    line([(532, 626), (560, 626), (560, 635), (672, 635)])
    symbol("valve", 685, 635, kind="control")
    text(660, 660, "FV-102", bold=True)
    line([(698, 635), (718, 635)])
    symbol("valve", 730, 635, kind="sol_no")
    text(716, 672, "XV-104", bold=True)
    line([(743, 635), (760, 635), (760, 520), (898, 520)], arrow=True)
    text(780, 512, "column feed, tray 6", 11, color=GREY)
    symbol("inst", 685, 555, letters="LIC", num="101", kind="dcs")
    text(685, 591, "R_LIC", 10, color="#777", anchor="middle")
    line([(685, 575), (685, 612)], "sig")
    line([(665, 555), (560, 555), (560, 470), (522, 470)], "sig")
    text(528, 462, "LT-101", 10, color=GREY)

    # ------------------------------------------------------------------ column T-201
    box(900, 180, 80, 650, r=40)
    for i in range(10):
        line([(904, 240 + 55 * i), (976, 240 + 55 * i)], "tray")
    text(986, 244, "1", 10, color="#666")
    text(986, 519, "6 (feed)", 10, color="#666")
    text(986, 739, "10", 10, color="#666")
    text(892, 166, "T-201", 16, bold=True, anchor="end")
    text(892, 182, "10 trays", 11, color="#444", anchor="end")

    # condenser E-201 (PV-201, COL_PIC) and reflux drum V-201
    line([(940, 180), (940, 120), (1110, 120)])
    line([(1250, 120), (1300, 120), (1300, 205)], arrow=True)
    box(1110, 98, 140, 44, r=22, width=2)
    line([(1124, 120), (1236, 120)], "cw")
    text(1180, 88, "E-201 condenser", bold=True, anchor="middle")
    line([(1420, 72), (1236, 72), (1236, 98)], "cw", arrow=True)
    text(1430, 76, "Cooling water", color=CW)
    line([(1130, 142), (1130, 164), (1200, 164)], "cw", arrow=True)
    text(1206, 168, "CW return", 11, color=CW)
    symbol("valve", 1330, 72, kind="control")
    text(1311, 97, "PV-201", bold=True)
    symbol("inst", 1330, 160, letters="PIC", num="201", kind="dcs")
    text(1362, 164, "COL_PIC", 10, color="#777")
    line([(1330, 140), (1330, 128), (1372, 128), (1372, 52), (1340, 52)], "sig")   # to PV-201's actuator
    line([(1310, 160), (1290, 160), (1290, 140), (1010, 140), (1010, 185), (982, 185)], "sig")
    text(988, 198, "PT-201", 10, color=GREY)

    box(1240, 210, 150, 56, r=28)
    text(1315, 238, "V-201", 14, bold=True, anchor="middle")
    text(1315, 254, "reflux drum", 10, color="#444", anchor="middle")

    # reflux: XV-201 (SIS), P-201A/B, FV-201 (TOP_TIC)
    line([(1300, 266), (1300, 330), (1253, 330)])
    symbol("valve", 1240, 330, kind="sol_no")
    text(1222, 358, "XV-201", bold=True)
    line([(1227, 330), (1186, 330)])
    symbol("pump", 1170, 330, mirror=True)
    text(1142, 378, "P-201A/B", bold=True)
    line([(1154, 316), (1120, 316), (1120, 328), (1068, 328)])
    symbol("valve", 1055, 328, kind="control")
    text(1036, 353, "FV-201", bold=True)
    text(1040, 365, "reflux", 10, color=GREY)
    line([(1042, 328), (1010, 328), (1010, 240), (982, 240)], arrow=True)
    symbol("inst", 1055, 430, letters="TIC", num="201", kind="dcs")
    text(1055, 466, "TOP_TIC", 10, color="#777", anchor="middle")
    line([(1075, 430), (1090, 430), (1090, 302), (1064, 302)], "sig")             # to FV-201's actuator
    line([(1035, 430), (1020, 430), (1020, 260), (982, 260)], "sig")
    text(988, 272, "TT-201", 10, color=GREY)

    # distillate: P-202A/B, XV-202 (SIS), FV-202 (DR_LIC)
    line([(1360, 266), (1360, 400), (1400, 400)])
    symbol("pump", 1416, 400)
    text(1388, 448, "P-202A/B", bold=True)
    line([(1432, 386), (1460, 386), (1460, 400), (1484, 400)])
    symbol("valve", 1496, 400, kind="sol_no")
    text(1474, 425, "XV-202", bold=True)
    line([(1508, 400), (1532, 400)])
    symbol("valve", 1545, 400, kind="control")
    text(1528, 425, "FV-202", bold=True)
    line([(1558, 400), (1660, 400)], arrow=True)
    text(1574, 374, "Distillate")
    text(1574, 389, "x ≈ 0.94 acetone", 11, color=GREY)
    symbol("inst", 1545, 320, letters="LIC", num="202", kind="dcs")
    text(1577, 324, "DR_LIC", 10, color="#777")
    line([(1545, 340), (1545, 377)], "sig")
    line([(1525, 320), (1430, 320), (1430, 250), (1390, 250)], "sig")
    text(1396, 262, "LT-202", 10, color=GREY)
    symbol("inst", 1620, 455, letters="AI", num="201", kind="field")
    line([(1620, 439), (1620, 400)], "sig")
    text(1590, 490, "purity (DIST_X_L)", 10, color="#777")

    # ------------------------------------------------------------------ reboiler E-202 and bottoms
    line([(940, 830), (940, 880), (1060, 880)])
    box(1060, 845, 180, 70, r=12)
    line([(1080, 892)] + [(1080 + 15 * i, 878 if i % 2 else 892) for i in range(1, 9)], "steam")
    text(1150, 870, "E-202", 14, bold=True, anchor="middle")
    text(1196, 910, "kettle reboiler", 10, color="#444", anchor="middle")
    line([(1150, 845), (1150, 700), (982, 700)], arrow=True)
    text(1160, 740, "vapour return", 11, color=GREY)

    # reboiler steam: XV-204 (SIS), TV-202 (BOT_TIC)
    text(880, 1000, "Steam (5.7 bar)", color=STEAM)
    line([(960, 1010), (1008, 1010)], "steam")
    symbol("valve", 1020, 1010, kind="sol_no")
    text(1000, 1040, "XV-204", bold=True)
    line([(1032, 1010), (1080, 1010)], "steam")
    symbol("valve", 1093, 1010, kind="control")
    text(1074, 1040, "TV-202", bold=True)
    line([(1106, 1010), (1130, 1010), (1130, 915)], "steam", arrow=True)
    symbol("inst", 1010, 950, letters="TIC", num="202", kind="dcs")
    text(968, 955, "BOT_TIC", 10, color="#777", anchor="end")
    line([(1030, 950), (1093, 950), (1093, 987)], "sig")
    line([(1010, 930), (1010, 780), (982, 780)], "sig")
    text(988, 776, "TT-202", 10, color=GREY)

    # bottoms: P-203A/B, XV-203 (SIS), FV-203 (RB_LIC)
    line([(1240, 905), (1270, 905), (1270, 960), (1302, 960)])
    symbol("pump", 1316, 960)
    text(1288, 1008, "P-203A/B", bold=True)
    line([(1332, 946), (1360, 946), (1360, 960), (1394, 960)])
    symbol("valve", 1406, 960, kind="sol_no")
    text(1384, 985, "XV-203", bold=True)
    line([(1418, 960), (1452, 960)])
    symbol("valve", 1465, 960, kind="control")
    text(1446, 985, "FV-203", bold=True)
    line([(1478, 960), (1660, 960)], arrow=True)
    text(1500, 932, "Bottoms")
    text(1500, 947, "IPA, x ≈ 0.04 acetone", 11, color=GREY)
    symbol("inst", 1465, 870, letters="LIC", num="203", kind="dcs")
    text(1497, 874, "RB_LIC", 10, color="#777")
    line([(1465, 890), (1465, 937)], "sig")
    line([(1445, 870), (1260, 870), (1260, 880), (1242, 880)], "sig")
    text(1250, 862, "LT-203", 10, color=GREY)
    symbol("inst", 1600, 1020, letters="AI", num="202", kind="field")
    line([(1600, 1004), (1600, 960)], "sig")
    text(1622, 1024, "(BTM_X_H)", 10, color="#777")

    # ------------------------------------------------------------------ SIS: one switch + one interlock per SIF
    def sif(cause, sw_letters, sw_num, sw, il, taps, label_xy, label_anchor="start"):
        r = rows[cause]
        group({"data-sif": cause})
        line(taps, "sis")
        line([(sw[0] + (-18 if il[0] < sw[0] else 18), sw[1]), (il[0] + (15 if il[0] < sw[0] else -15), il[1])], "sis")
        symbol("inst", sw[0], sw[1], letters=sw_letters, num=sw_num, kind="switch")
        symbol("interlock", il[0], il[1], label=INTERLOCK[cause])
        text(label_xy[0], label_xy[1], f"{r['trip'].split(' ', 1)[1]}", 10, color=RED, anchor=label_anchor)
        end_group()

    sif("R_T_HH", "TSHH", "101", (318, 200), (262, 200), [(334, 200), (380, 200)], (244, 204), "end")
    sif("R_P_HH", "PSHH", "101", (620, 300), (676, 300), [(604, 300), (545, 300), (545, 280), (522, 280)], (696, 304))
    sif("R_P_HHH", "PSHHH", "101", (620, 355), (676, 355), [(604, 355), (545, 355), (545, 300)], (696, 359))
    sif("R_L_HH", "LSHH", "101", (318, 640), (262, 640), [(334, 640), (420, 640), (420, 510)], (244, 644), "end")
    sif("R_L_LL", "LSLL", "101", (318, 700), (262, 700), [(334, 700), (420, 700), (420, 640)], (244, 704), "end")
    sif("RB_L_HH", "LSHH", "203", (1100, 800), (1050, 800), [(1100, 816), (1100, 845)], (1050, 780), "middle")
    sif("RB_L_LL", "LSLL", "203", (1200, 800), (1250, 800), [(1200, 816), (1200, 845)], (1250, 780), "middle")
    sif("DR_L_HH", "LSHH", "202", (1470, 215), (1526, 215), [(1454, 215), (1410, 215), (1410, 232), (1390, 232)], (1546, 219))
    sif("DR_L_LL", "LSLL", "202", (1470, 265), (1526, 265), [(1454, 265), (1410, 265), (1410, 232)], (1546, 269))
    sif("COL_P_HH", "PSHH", "201", (840, 250), (790, 250), [(856, 250), (900, 250)], (770, 254), "end")
    sif("COL_L_LL", "LSLL", "210", (840, 760), (790, 760), [(856, 760), (900, 760)], (770, 764), "end")

    # interlocks acting on each final element (from the cause & effect matrix)
    acting: dict[str, list[str]] = {}
    for r in matrix():
        for col, eff in r["effects"].items():
            if not eff:
                continue
            tag = FINAL_ELEMENT[col] if col != "Fresh feed" else ("FV-101" if eff.startswith("≤") else "XV-101")
            acting.setdefault(tag, []).append(r["interlock"])
    for tag, (x, y) in REF_AT.items():
        if tag in acting:
            text(x, y, " ".join(acting[tag]), 10, color=RED)

    # live values (filled in by the console's P&ID view)
    group({"class": "live"})
    for loop, (x, y, anchor) in LIVE.items():
        text(x, y, "", 11, bold=True, color="#1565c0", anchor=anchor, attrs={"data-pv": loop})
    end_group()

    # ------------------------------------------------------------------ legend
    lx, ly = 24, 770
    box(lx, ly, 470, 300, width=1)
    text(lx + 12, ly + 22, "Legend", bold=True)
    for i, (style, label) in enumerate([("proc", "process line"), ("sig", "instrument signal (BPCS)"),
                                        ("sis", "SIS signal"), ("steam", "heating / steam"), ("cw", "cooling water")]):
        line([(lx + 12, ly + 44 + 22 * i), (lx + 70, ly + 44 + 22 * i)], style)
        text(lx + 80, ly + 48 + 22 * i, label, color=RED if style == "sis" else INK)
    symbol("inst", lx + 280, ly + 50, letters="", num="", kind="dcs", scale=0.7)
    text(lx + 302, ly + 54, "controller (DCS)")
    symbol("inst", lx + 280, ly + 84, letters="", num="", kind="field", scale=0.8)
    text(lx + 302, ly + 88, "field instrument / switch")
    symbol("interlock", lx + 280, ly + 118, label="I")
    text(lx + 302, ly + 122, "SIS interlock (C&E row)", color=RED)
    symbol("valve", lx + 40, ly + 162, kind="control")
    text(lx + 62, ly + 166, "control valve")
    symbol("pump", lx + 40, ly + 196)
    text(lx + 62, ly + 200, "pump, A duty / B standby")
    symbol("psv", lx + 40, ly + 228)
    text(lx + 62, ly + 236, "relief valve")
    symbol("valve", lx + 280, ly + 160, kind="sol_no")
    text(lx + 302, ly + 164, "shutdown valve, open")
    symbol("valve", lx + 280, ly + 200, kind="sol_nc")
    text(lx + 302, ly + 204, "shutdown valve, closed")
    text(lx + 260, ly + 236, "Red labels: interlocks acting on it.", 10.5, color="#444")
    text(lx + 12, ly + 268, "Grey ids under controllers: the simulator's loop ids (R_TIC …).", 10.5, color="#444")
    text(lx + 12, ly + 286, "Cause & effect matrix, alarms and loop tables: docs/pid.md.", 10.5, color="#444")


FINAL_ELEMENT = {"Reactor heating": "XV-103", "Emergency cooling": "XV-102", "H₂ vent": "PCV-101",
                 "Blowdown": "BDV-101", "Reactor outlet / column feed": "XV-104", "Reflux": "XV-201",
                 "Distillate": "XV-202", "Bottoms": "XV-203", "Reboiler steam": "XV-204",
                 "Condenser cooling water": "PV-201"}
REF_AT = {"XV-101": (313, 364), "FV-101": (226, 364), "XV-103": (236, 517), "XV-102": (290, 610),
          "PCV-101": (640, 141), "BDV-101": (660, 185), "XV-104": (716, 684), "XV-201": (1222, 370),
          "XV-202": (1474, 437), "XV-203": (1384, 997), "XV-204": (1000, 1052), "PV-201": (1311, 109)}
LIVE = {"R_TIC": (285, 414, "end"), "R_PIC": (724, 236, "start"), "R_LIC": (710, 559, "start"),
        "COL_PIC": (1362, 180, "start"), "TOP_TIC": (1104, 434, "start"), "DR_LIC": (1577, 340, "start"),
        "BOT_TIC": (968, 970, "end"), "RB_LIC": (1497, 890, "start")}

# ---------------------------------------------------------------------------------------------- SVG
SVG_DEFS = f"""  <defs>
    <marker id="pid-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{INK}"/></marker>
    <marker id="pid-arrow-cw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{CW}"/></marker>
    <marker id="pid-arrow-steam" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{STEAM}"/></marker>
    <g id="pid-body"><path d="M-12 -8 L12 8 L12 -8 L-12 8 Z" stroke="{INK}" stroke-width="1.6"/></g>
    <g id="pid-control"><use href="#pid-body" fill="#fff"/><path d="M0 0 V-16" stroke="{INK}" stroke-width="1.4"/>
      <path d="M-9 -16 A9 7 0 0 1 9 -16 Z" fill="#fff" stroke="{INK}" stroke-width="1.4"/></g>
    <g id="pid-sol_no"><use href="#pid-body" fill="#fff"/><path d="M0 0 V-14" stroke="{INK}" stroke-width="1.4"/>
      <rect x="-7" y="-28" width="14" height="14" fill="#fff" stroke="{INK}" stroke-width="1.3"/>
      <text y="-17.5" text-anchor="middle" font-size="10" font-weight="bold" fill="{INK}">S</text></g>
    <g id="pid-sol_nc"><use href="#pid-body" fill="{INK}"/><path d="M0 0 V-14" stroke="{INK}" stroke-width="1.4"/>
      <rect x="-7" y="-28" width="14" height="14" fill="#fff" stroke="{INK}" stroke-width="1.3"/>
      <text y="-17.5" text-anchor="middle" font-size="10" font-weight="bold" fill="{INK}">S</text></g>
    <g id="pid-psv"><path d="M-10 -8 L10 -8 L0 8 Z M0 8 L-10 22 H10 Z" fill="#fff" stroke="{INK}" stroke-width="1.5"/>
      <path d="M0 -8 V-18 M-5 -10 L5 -13 M-5 -14 L5 -17" stroke="{INK}" stroke-width="1.2" fill="none"/></g>
    <g id="pid-pump"><circle r="14" fill="#fff" stroke="{INK}" stroke-width="1.6"/><path d="M0 -14 H16" stroke="{INK}" stroke-width="1.6"/>
      <path d="M-8 12 L-14 22 H14 L8 12" fill="none" stroke="{INK}" stroke-width="1.4"/></g>
  </defs>"""


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def svg() -> str:
    build()
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="Arial, Helvetica, sans-serif" font-size="12">',
           "  <!-- OperAItor plant P&ID, generated by docs/pid/build_pid.py (edit it there, or the draw.io file). -->",
           SVG_DEFS,
           f'  <rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'  <rect x="8" y="8" width="{W - 16}" height="{H - 16}" fill="none" stroke="{INK}" stroke-width="2"/>',
           '  <text x="24" y="38" font-size="20" font-weight="bold">OperAItor process plant: reactor R-101 and distillation '
           'column T-201</text>',
           f'  <text x="24" y="58" fill="#444">Piping and instrumentation diagram (ISA 5.1 style), drawn from the simulator '
           f'(operaitor/plant). Process modelled on isopropanol → acetone + H₂.</text>',
           f'  <g transform="translate(0 {DY})">']
    for kind, d in ITEMS:
        if kind == "line":
            stroke, width, dash = LINE_STYLES[d["style"]]
            pts = " ".join(f"{'M' if i == 0 else 'L'}{x:g} {y:g}" for i, (x, y) in enumerate(d["pts"]))
            arrow = ""
            if d["arrow"]:
                arrow = f' marker-end="url(#pid-arrow{"-cw" if d["style"] == "cw" else "-steam" if d["style"] == "steam" else ""})"'
            out.append(f'    <path d="{pts}" fill="none" stroke="{stroke}" stroke-width="{width}"'
                       + (f' stroke-dasharray="{dash}"' if dash else "") + arrow + "/>")
        elif kind == "text":
            attrs = "".join(f' {k}="{_esc(v)}"' for k, v in d["attrs"].items())
            style = (' font-weight="bold"' if d["bold"] else "") + (f' fill="{d["color"]}"' if d["color"] != INK else "")
            out.append(f'    <text x="{d["x"]:g}" y="{d["y"]:g}" font-size="{d["size"]:g}" text-anchor="{d["anchor"]}"'
                       f'{style}{attrs}>{_esc(d["s"])}</text>')
        elif kind == "box":
            out.append(f'    <rect x="{d["x"]}" y="{d["y"]}" width="{d["w"]}" height="{d["h"]}" rx="{d["r"]}" '
                       f'fill="{d["fill"]}" stroke="{d["stroke"]}" stroke-width="{d["width"]}"/>')
        elif kind == "valve":
            out.append(f'    <use href="#pid-{d["kind"]}" x="{d["x"]}" y="{d["y"]}"/>')
        elif kind == "psv":
            out.append(f'    <use href="#pid-psv" x="{d["x"]}" y="{d["y"]}"/>')
        elif kind == "pump":
            if d.get("mirror"):
                out.append(f'    <use href="#pid-pump" transform="translate({d["x"]} {d["y"]}) scale(-1 1)"/>')
            else:
                out.append(f'    <use href="#pid-pump" x="{d["x"]}" y="{d["y"]}"/>')
        elif kind == "inst":
            k, x, y, sc = d["kind"], d["x"], d["y"], d.get("scale", 1)
            r = (20 if k == "dcs" else 18 if k == "switch" else 16) * sc
            fill = "fill: var(--sis-fill, #fff)" if k == "switch" else "fill: #fff"
            stroke = RED if k == "switch" else INK
            out.append(f'    <circle cx="{x}" cy="{y}" r="{r:g}" style="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
            if k == "dcs":
                out.append(f'    <path d="M{x - r:g} {y} H{x + r:g}" stroke="{INK}" stroke-width="1.2"/>')
            if d["letters"]:
                fs = 11 if k == "dcs" else 8 if len(d["letters"]) > 4 else 9
                col = RED if k == "switch" else INK
                out.append(f'    <text x="{x}" y="{y - 3}" font-size="{fs}" text-anchor="middle" font-weight="bold" fill="{col}">'
                           f'{d["letters"]}</text>')
                out.append(f'    <text x="{x}" y="{y + 11}" font-size="{fs}" text-anchor="middle" fill="{col}">{d["num"]}</text>')
        elif kind == "interlock":
            x, y = d["x"], d["y"]
            out.append(f'    <path d="M{x} {y - 15} L{x + 15} {y} L{x} {y + 15} L{x - 15} {y} Z" style="fill: var(--sis-fill, #fff)" '
                       f'stroke="{RED}" stroke-width="1.6"/>')
            out.append(f'    <text x="{x}" y="{y + 4}" font-size="11" font-weight="bold" text-anchor="middle" fill="{RED}">'
                       f'{d["label"]}</text>')
        elif kind == "group":
            out.append("    <g" + "".join(f' {k}="{_esc(v)}"' for k, v in d.items()) + ">")
        elif kind == "end_group":
            out.append("    </g>")
    out.append("  </g>")
    out.append(f'  <g transform="translate(540 {H - 38})"><rect x="-10" y="-34" width="380" height="50" fill="#fff" stroke="{INK}"/>'
               '<text x="0" y="-14" font-weight="bold">OperAItor · AISL, Imperial College London</text>'
               '<text x="0" y="4" fill="#444" font-size="11">P&amp;ID of the simulated plant (beta) · not for construction</text></g>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------- draw.io
def drawio() -> str:
    build()
    cells, n = [], [0]

    def cid() -> str:
        n[0] += 1
        return f"c{n[0]}"

    def vertex(value, style, x, y, w, h):
        cells.append(f'<mxCell id="{cid()}" value="{_esc(value)}" style="{style}" vertex="1" parent="1">'
                     f'<mxGeometry x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" as="geometry"/></mxCell>')

    def edge(pts, style):
        (x0, y0), (x1, y1), mid = pts[0], pts[-1], pts[1:-1]
        arr = "".join(f'<mxPoint x="{x:g}" y="{y:g}"/>' for x, y in mid)
        cells.append(f'<mxCell id="{cid()}" style="{style}" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
                     f'<mxPoint x="{x0:g}" y="{y0:g}" as="sourcePoint"/><mxPoint x="{x1:g}" y="{y1:g}" as="targetPoint"/>'
                     + (f'<Array as="points">{arr}</Array>' if arr else "") + "</mxGeometry></mxCell>")

    def txt(x, y, s, size=12, bold=False, color=INK, anchor="start"):
        w = max(8.0, len(s) * size * 0.56 + 6)
        left = x - (w / 2 if anchor == "middle" else w if anchor == "end" else 0) - (0 if anchor != "start" else 2)
        align = {"start": "left", "middle": "center", "end": "right"}[anchor]
        vertex(s, f"text;html=1;align={align};verticalAlign=middle;fontSize={size:g};fontColor={color};"
                  f"fontStyle={1 if bold else 0};spacing=0;", left, y - size, w, size + 4)

    title = [(24, 38, "OperAItor process plant: reactor R-101 and distillation column T-201", 20, True, INK),
             (24, 58, "Piping and instrumentation diagram (ISA 5.1 style), drawn from the simulator (operaitor/plant). "
                      "Process modelled on isopropanol → acetone + H₂.", 12, False, "#444")]
    vertex("", f"rounded=0;fillColor=none;strokeColor={INK};strokeWidth=2;", 8, 8, W - 16, H - 16)
    for x, y, s, size, bold, color in title:
        txt(x, y, s, size, bold, color)
    for kind, d in ITEMS:
        if kind == "line":
            stroke, width, dash = LINE_STYLES[d["style"]]
            style = (f"endArrow={'block;endFill=1;endSize=6' if d['arrow'] else 'none'};html=1;rounded=0;strokeColor={stroke};"
                     f"strokeWidth={width};" + (f"dashed=1;dashPattern={dash};" if dash else ""))
            edge([(x, y + DY) for x, y in d["pts"]], style)
        elif kind == "text":
            if d["s"]:
                txt(d["x"], d["y"] + DY, d["s"], d["size"], d["bold"], d["color"], d["anchor"])
        elif kind == "box":
            vertex("", f"rounded={1 if d['r'] else 0};absoluteArcSize=1;arcSize={2 * d['r']};fillColor={d['fill']};"
                       f"strokeColor={d['stroke']};strokeWidth={d['width']};", d["x"], d["y"] + DY, d["w"], d["h"])
        elif kind == "valve":
            act = {"control": "diaph", "sol_no": "solenoid", "sol_nc": "solenoid"}[d["kind"]]
            fill = "fillColor=#000000;" if d["kind"] == "sol_nc" else "fillColor=#ffffff;"
            vertex("", f"shape=mxgraph.pid2valves.valve;valveType=gate;actuator={act};{fill}strokeColor={INK};html=1;",
                   d["x"] - 13, d["y"] + DY - 23, 26, 31)
        elif kind == "psv":
            vertex("", f"shape=mxgraph.pid2valves.valve;valveType=angle;actuator=spring;strokeColor={INK};html=1;",
                   d["x"] - 13, d["y"] + DY - 13, 26, 30)
        elif kind == "pump":
            vertex("", "shape=mxgraph.pid.pumps.centrifugal_pump_1;html=1;" + ("flipH=1;" if d.get("mirror") else ""),
                   d["x"] - 16, d["y"] + DY - 16, 32, 32)
        elif kind == "inst":
            k, sc = d["kind"], d.get("scale", 1)
            r = (20 if k == "dcs" else 18 if k == "switch" else 16) * sc
            mount = "room" if k == "dcs" else "field"
            col = RED if k == "switch" else INK
            value = f"<b>{d['letters']}</b><br>{d['num']}" if d["letters"] else ""
            vertex(value, f"shape=mxgraph.pid2inst.discInst;mounting={mount};html=1;whiteSpace=wrap;fontSize="
                          f"{11 if k == 'dcs' else 9};strokeColor={col};fontColor={col};",
                   d["x"] - r, d["y"] + DY - r, 2 * r, 2 * r)
        elif kind == "interlock":
            vertex(d["label"], f"rhombus;html=1;strokeColor={RED};fontColor={RED};fontStyle=1;fontSize=11;strokeWidth=1.6;",
                   d["x"] - 15, d["y"] + DY - 15, 30, 30)
    vertex("<b>OperAItor · AISL, Imperial College London</b><br><font color=\"#444444\" style=\"font-size:11px\">"
           "P&amp;ID of the simulated plant (beta) · not for construction</font>",
           f"rounded=0;html=1;align=left;spacingLeft=10;fillColor=#ffffff;strokeColor={INK};", 530, H - 72, 380, 50)
    model = (f'<mxGraphModel dx="1700" dy="1140" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
             f'fold="1" page="1" pageScale="1" pageWidth="{W}" pageHeight="{H}" math="0" shadow="0"><root>'
             '<mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel>")
    return ('<mxfile host="OperAItor" agent="docs/pid/build_pid.py"><diagram id="pid" name="P&amp;ID">'
            + model + "</diagram></mxfile>\n")


SVG_OUT = ROOT / "frontend" / "public" / "static" / "pid.svg"
DRAWIO_OUT = ROOT / "docs" / "pid" / "pid.drawio"

if __name__ == "__main__":
    SVG_OUT.write_text(svg(), encoding="utf-8")
    DRAWIO_OUT.write_text(drawio(), encoding="utf-8")
    print(f"wrote {SVG_OUT.relative_to(ROOT)} and {DRAWIO_OUT.relative_to(ROOT)}")
