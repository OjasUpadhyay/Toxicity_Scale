"""PDF report builders for AuraFlow.

build_readings_pdf(analysis)   -> bytes   water-quality report for the visitor's readings
build_evaluation_pdf(report)   -> bytes   screening-evaluation report (metrics, line plots, SDG 3)

Needs the `reportlab` package. Charts are drawn as vector graphics, so nothing
else (matplotlib, a browser) is required on the server.
"""
import datetime
import io
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Circle, Drawing, Group, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#14202e")
MUTED = colors.HexColor("#5b6a7a")
ACCENT = colors.HexColor("#0b7f9a")
RULE = colors.HexColor("#c9d4de")
ZEBRA = colors.HexColor("#f3f7fa")
OK, WARN, BAD = "#0a7a58", "#b7791f", "#b3243f"
LEVEL_COLOR = {"low": OK, "mid": WARN, "high": BAD}
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=_ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=11, textColor=MUTED, spaceBefore=4)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=21, leading=25, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=ACCENT, spaceBefore=14, spaceAfter=6)
H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=2)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.3, leading=10.8)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
KPI = ParagraphStyle("kpi", parent=BODY, fontName="Helvetica-Bold", fontSize=19, leading=22, textColor=ACCENT)
LIST = ParagraphStyle("list", parent=BODY, leftIndent=14, bulletIndent=2, spaceAfter=3)


def _t(value):
    """Make arbitrary user text safe for a Paragraph and for the built-in PDF fonts."""
    s = str(value).encode("latin-1", "replace").decode("latin-1")
    return escape(s)


def P(text, style=BODY):
    return Paragraph(text, style)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 9.5 * mm, "AuraFlow - demonstration report. Not a drinking-water safety certificate.")
    canvas.drawRightString(PAGE_W - MARGIN, 9.5 * mm, "Page %d" % doc.page)
    canvas.restoreState()


def _doc(buf, title):
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=16 * mm,
                             bottomMargin=20 * mm, title=title, author="AuraFlow", subject="Water quality")


def _table(data, widths, right_from=1, header=True, extra=None):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.3),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"),
    ]
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.3), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6eef4")),
                  ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA])]
    t.setStyle(TableStyle(style + (extra or [])))
    return t


# ---------------------------------------------------------------- line plots

def line_plot(series, xlabel, ylabel, width=CONTENT_W, height=64 * mm, xmax=1.0, ymax=1.0, bands=None,
              hlines=None, vline=None, xticks=None, xdec=1, ydec=1, markers=None):
    """series: [(name, [(x, y), ...], hex_colour, dashed_bool)]"""
    d = Drawing(width, height)
    L, R, T, B = 36, 12, 8, 46
    pw, ph = width - L - R, height - T - B
    X = lambda x: L + (x / xmax) * pw
    Y = lambda y: B + (y / ymax) * ph

    for lo, hi, col in (bands or []):
        d.add(Rect(L, Y(lo), pw, Y(hi) - Y(lo), fillColor=colors.HexColor(col), fillOpacity=0.12, strokeColor=None))
    for i in range(6):
        g = i / 5
        d.add(Line(L, Y(g * ymax), L + pw, Y(g * ymax), strokeColor=RULE, strokeWidth=0.5))
        d.add(String(L - 5, Y(g * ymax) - 2.5, "%.*f" % (ydec, g * ymax), fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="end"))
        if not xticks:
            d.add(String(X(g * xmax), B - 11, "%.*f" % (xdec, g * xmax), fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="middle"))
    for x, label in (xticks or []):
        d.add(String(X(x), B - 11, label, fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="middle"))
    for y, label in (hlines or []):
        d.add(Line(L, Y(y), L + pw, Y(y), strokeColor=MUTED, strokeWidth=0.6, strokeDashArray=[3, 3]))
        d.add(String(L + pw - 3, Y(y) + 3, label, fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="end"))
    if vline is not None:
        d.add(Line(X(vline), B, X(vline), B + ph, strokeColor=MUTED, strokeWidth=0.8, strokeDashArray=[3, 3]))
    for name, pts, col, dashed in series:
        flat = []
        for x, y in pts:
            flat += [X(x), Y(y)]
        if len(flat) >= 4:
            d.add(PolyLine(flat, strokeColor=colors.HexColor(col), strokeWidth=1.7, strokeDashArray=[4, 3] if dashed else None))
    for x, y, col in (markers or []):
        d.add(Circle(X(x), Y(y), 3.2, fillColor=colors.HexColor(col), strokeColor=colors.white, strokeWidth=0.8))

    d.add(String(L + pw / 2, B - 24, xlabel, fontName="Helvetica", fontSize=8, fillColor=MUTED, textAnchor="middle"))
    g = Group(String(0, 0, ylabel, fontName="Helvetica", fontSize=8, fillColor=MUTED, textAnchor="middle"))
    g.translate(9, B + ph / 2)
    g.rotate(90)
    d.add(g)

    x = L
    for name, pts, col, dashed in series:           # legend row
        d.add(Line(x, 7, x + 14, 7, strokeColor=colors.HexColor(col), strokeWidth=2, strokeDashArray=[4, 3] if dashed else None))
        d.add(String(x + 18, 4.5, name, fontName="Helvetica", fontSize=7.5, fillColor=INK))
        x += 18 + stringWidth(name, "Helvetica", 7.5) + 16
    return d


def _stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%d %B %Y, %H:%M UTC")


# ---------------------------------------------------------------- readings report

def build_readings_pdf(analysis):
    rows, stats = analysis["rows"], analysis["stats"]
    buf = io.BytesIO()
    doc = _doc(buf, "AuraFlow water quality report")
    s = [P("AuraFlow water quality report", H1),
         P("Generated %s. Screening of %d monitoring node(s) on pH, turbidity, metals and BOD." % (_stamp(), stats["total"]), SMALL),
         Spacer(1, 8)]

    kp = [[P(str(stats["total"]), KPI), P("%.1f%%" % stats["average"], KPI), P(str(stats["caution"]), KPI), P(str(stats["high"]), KPI)],
          [P("Nodes", SMALL), P("Average score", SMALL), P("Need review", SMALL), P("High risk", SMALL)]]
    k = Table(kp, colWidths=[CONTENT_W / 4] * 4)
    k.setStyle(TableStyle([("LINEBEFORE", (1, 0), (-1, -1), 0.6, RULE), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    s += [k, Spacer(1, 6)]

    # score line chart
    n = len(rows)
    ticks = [(i, str(i + 1)) for i in range(n)]
    if n > 15:
        step = -(-n // 15)
        ticks = [t for t in ticks if t[0] % step == 0]
    s.append(KeepTogether([P("Score across nodes", H2), line_plot(
        [("Score (%)", [(i, r["wti"]) for i, r in enumerate(rows)], "#0b7f9a", False)],
        "Node (order in the table below)", "Risk score (%)", xmax=max(n - 1, 1), ymax=100, ydec=0, xticks=ticks,
        bands=[(0, 25, OK), (25, 50, WARN), (50, 100, BAD)],
        hlines=[(25, "Caution from 25"), (50, "High risk from 50")],
        markers=[(i, r["wti"], LEVEL_COLOR[r["level"]]) for i, r in enumerate(rows)]),
        P("Green, amber and red points are low risk (under 25), caution (25 to 50) and high risk (over 50). "
               "Higher is worse. The score ranks places against each other; it is not a safety rating.", SMALL)]))

    # readings table
    s.append(P("Readings", H2))
    data = [["#", "Location", "pH", "Turb. (NTU)", "Metals (ppm)", "BOD (mg/L)", "Score", "Status"]]
    extra = []
    for i, r in enumerate(rows):
        data.append([str(i + 1), P(_t(r["location"]), CELL), "%.2f" % r["ph"], "%.2f" % r["turbidity"], "%.3f" % r["metals"],
                     "%.1f" % r["bod"], "%.1f%%" % r["wti"], P(_t(r["status"]), CELL)])
        extra.append(("TEXTCOLOR", (6, i + 1), (6, i + 1), colors.HexColor(LEVEL_COLOR[r["level"]])))
    w = CONTENT_W
    s.append(_table(data, [8 * mm, w * 0.27, 14 * mm, 21 * mm, 22 * mm, 20 * mm, 16 * mm, w - (8 * mm + w * 0.27 + 14 * mm + 21 * mm + 22 * mm + 20 * mm + 16 * mm)],
                    right_from=2, extra=extra + [("ALIGN", (7, 0), (7, -1), "LEFT")]))

    # drinking check for the highest-risk nodes
    top = sorted(range(n), key=lambda i: -rows[i]["wti"])[:15]
    s.append(P("Is it drinkable? Highest-risk nodes", H2))
    s.append(P("Each check compares the readings with drinking-water guidance (BIS IS 10500, WHO guideline values, CPCB source classes). "
               "Only four measurements are covered, so the best possible result is \"passes these checks\", never \"safe\".%s"
               % (" Showing the %d highest-scoring nodes." % len(top) if n > len(top) else ""), BODY))
    for i in top:
        r, dk = rows[i], rows[i]["drink"]
        blk = [P("%d. %s - score %.1f%%" % (i + 1, _t(r["location"]), r["wti"]), H3),
               P("<font color='%s'><b>%s.</b></font> %s" % (LEVEL_COLOR[dk["level"]], _t(dk["label"]), _t(dk["summary"])), BODY), Spacer(1, 3)]
        cd = [["Measurement", "Reading", "Result", "What it means"]]
        ex = []
        for j, c in enumerate(dk["checks"]):
            cd.append([c["name"], c["value"], c["status"].capitalize(), P(_t(c["note"]), CELL)])
            col = {"pass": OK, "check": WARN, "fail": BAD}[c["status"]]
            ex += [("TEXTCOLOR", (2, j + 1), (2, j + 1), colors.HexColor(col)), ("FONT", (2, j + 1), (2, j + 1), "Helvetica-Bold", 8.3)]
        blk.append(_table(cd, [24 * mm, 24 * mm, 16 * mm, CONTENT_W - 64 * mm], right_from=9, extra=ex + [("ALIGN", (1, 0), (1, -1), "RIGHT")]))
        blk.append(P("<b>Next steps</b>", H3))
        for k2, st in enumerate(dk["steps"], 1):
            blk.append(Paragraph(_t(st), LIST, bulletText="%d." % k2))
        s.append(KeepTogether(blk[:4]))
        s += blk[4:]

    s.append(P("How the score works", H2))
    s.append(P("Each measurement is scaled from 0 to 1 against a demonstration limit (pH 3 units from neutral, 100 NTU, 1 ppm metals, 150 mg/L BOD) "
               "and combined with weights of 20%, 25%, 35% and 20%. If any single measurement reaches its limit, the score is at least 60, "
               "so one bad reading cannot be averaged away. The score is a project-defined illustration, not an official BIS formula.", BODY))
    s.append(P("Data fingerprint (SHA-256 of the readings): %s" % analysis["sha256"], SMALL))
    s.append(Spacer(1, 4))
    s.append(P("<b>Demonstration only.</b> No sensor or valve is connected. Germs (coliforms, E. coli) and most chemicals are not tested. "
               "Confirm any drinking-water decision with an accredited laboratory.", SMALL))
    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ---------------------------------------------------------------- evaluation report

def _f(v):
    return "%.3f" % v


def _cm_table(title, m):
    inner = Table([["", "Pred. unsafe", "Pred. OK"], ["Unsafe", "TP %d" % m["tp"], "FN %d" % m["fn"]],
                   ["OK", "FP %d" % m["fp"], "TN %d" % m["tn"]]], colWidths=[14 * mm, 20 * mm, 18 * mm])
    inner.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 7.8), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.8),
                               ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                               ("BACKGROUND", (1, 1), (1, 1), colors.HexColor("#d7efe5")), ("BACKGROUND", (2, 2), (2, 2), colors.HexColor("#d7efe5")),
                               ("BACKGROUND", (2, 1), (2, 1), colors.HexColor("#f6d9de")), ("BACKGROUND", (1, 2), (1, 2), colors.HexColor("#f6d9de")),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return [P("<b>%s</b>" % title, SMALL), inner]


def build_evaluation_pdf(rep):
    M = rep["models"]
    v1, v2, ds = M["wti_v1"], M["wti_v2"], rep["dataset"]
    sf = v2["safety_first"]
    buf = io.BytesIO()
    doc = _doc(buf, "AuraFlow evaluation report")
    s = [P("AuraFlow evaluation report", H1),
         P("Generated %s. The water toxicity score is evaluated as a screening classifier. Positive class = unsafe water; "
           "missing it (a false negative) is the costly error, so recall (sensitivity) is the headline metric." % _stamp(), SMALL),
         P("Data: %d simulated samples (%d unsafe, %d acceptable) with simulated sensor noise." % (ds["n"], ds["unsafe"], ds["acceptable"]), SMALL),
         Spacer(1, 8)]

    kp = [[P(_f(v2["roc_auc"]), KPI), P(_f(v2["default_band"]["recall_sensitivity"]), KPI), P(_f(v2["default_band"]["specificity"]), KPI), P(_f(v1["default_band"]["recall_sensitivity"]), KPI)],
          [P("ROC-AUC, calibrated score", SMALL), P("Recall, calibrated score", SMALL), P("Specificity, calibrated score", SMALL), P("Recall, current score (band 25)", SMALL)]]
    k = Table(kp, colWidths=[CONTENT_W / 4] * 4)
    k.setStyle(TableStyle([("LINEBEFORE", (1, 0), (-1, -1), 0.6, RULE), ("LEFTPADDING", (0, 0), (-1, -1), 10)]))
    s += [k, Spacer(1, 8)]

    box = Table([[P("<b>Key finding.</b> At its published low-risk band, the current score flags only %.0f%% of unsafe samples, because its limits "
                    "are set for extreme pollution and its penalties are squared. A limit-calibrated version flags %.0f%% at %.0f%% specificity. "
                    "The data is simulated and the calibrated score shares its limits with the ground truth, so this shows internal consistency, "
                    "not independent validation." % (v1["default_band"]["recall_sensitivity"] * 100, v2["default_band"]["recall_sensitivity"] * 100,
                                                    v2["default_band"]["specificity"] * 100), BODY)]], colWidths=[CONTENT_W])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf6e7")), ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(WARN)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    s.append(box)

    s.append(KeepTogether([P("ROC curve", H2), line_plot([("Current score, AUC %s" % _f(v1["roc_auc"]), [tuple(p) for p in v1["roc_points"]], BAD, False),
                        ("Calibrated score, AUC %s" % _f(v2["roc_auc"]), [tuple(p) for p in v2["roc_points"]], "#0b7f9a", False),
                        ("Chance", [(0, 0), (1, 1)], "#8a96a3", True)],
                       "False positive rate (1 - specificity)", "Sensitivity (recall)", height=70 * mm),
        P("A curve that hugs the top-left corner catches unsafe water without many false alarms.", SMALL)]))

    sw = lambda key: [(r["threshold"] / 100.0, r[key]) for r in v2["threshold_sweep"]]
    s.append(KeepTogether([P("Threshold trade-off (calibrated score)", H2), line_plot([("Recall", sw("recall_sensitivity"), "#0b7f9a", False), ("Specificity", sw("specificity"), OK, False),
                        ("Precision", sw("precision"), WARN, False), ("F1", sw("f1"), BAD, False)],
                       "Alert threshold (score / 100)", "Metric", vline=sf["threshold"] / 100.0, height=64 * mm),
        P("Dashed line: safety-first threshold (%d), the highest alert level that keeps recall at or above %.2f." % (sf["threshold"], sf["min_recall_target"]), SMALL)]))

    if rep.get("noise_robustness"):
        nz = rep["noise_robustness"]
        s.append(KeepTogether([P("Robustness to sensor noise", H2), line_plot(
            [("Current score", [(r["sensor_noise"], r["wti_v1"]) for r in nz], BAD, False),
             ("Calibrated score", [(r["sensor_noise"], r["wti_v2"]) for r in nz], "#0b7f9a", False)],
            "Sensor noise (log-sd)", "ROC-AUC", xmax=0.4, xdec=2, height=58 * mm)]))

    s.append(P("Metrics", H2))
    md = [["Model / alert level", "Precision", "Recall", "Specificity", "F1", "Accuracy"]]
    for name, m in (("Current score, alert at 25 or more", v1["default_band"]), ("Calibrated score, alert at 25 or more", v2["default_band"]),
                    ("Calibrated score, safety-first (%d or more)" % sf["threshold"], sf),
                    ("App drinking check (stricter, flags borderline)", rep["rule_based_drinking_check"])):
        md.append([name] + [_f(m[k2]) for k2 in ("precision", "recall_sensitivity", "specificity", "f1", "accuracy")])
    s.append(_table(md, [CONTENT_W - 5 * 21 * mm] + [21 * mm] * 5))
    s.append(P("Precision = flagged samples that really were unsafe. Recall = unsafe samples that were flagged. Specificity = acceptable samples correctly left alone. "
               "F1 balances precision and recall. ROC-AUC (95%% CI) - current: %s (%s to %s); calibrated: %s (%s to %s)."
               % (_f(v1["roc_auc"]), v1["roc_auc_95ci"][0], v1["roc_auc_95ci"][1], _f(v2["roc_auc"]), v2["roc_auc_95ci"][0], v2["roc_auc_95ci"][1]), SMALL))

    s.append(P("Confusion matrices", H2))
    cm = Table([[_cm_table("Current score, 25 or more", v1["default_band"]), _cm_table("Calibrated score, 25 or more", v2["default_band"]),
                 _cm_table("App drinking check", rep["rule_based_drinking_check"])]], colWidths=[CONTENT_W / 3] * 3)
    cm.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    s.append(cm)
    s.append(P("Rows are the truth, columns the prediction. TP = unsafe caught, FN = unsafe missed, FP = false alarm, TN = acceptable left alone.", SMALL))

    pa1, pa2 = v1["interpretability"]["per_parameter_auc"], v2["interpretability"]["per_parameter_auc"]
    s.append(P("Interpretability", H2))
    s.append(_table([["Single-measurement ROC-AUC", "pH", "Turbidity", "Metals", "BOD"],
                     ["Current score"] + [_f(pa1[k2]) for k2 in ("ph", "turbidity", "metals", "bod")],
                     ["Calibrated score"] + [_f(pa2[k2]) for k2 in ("ph", "turbidity", "metals", "bod")]],
                    [CONTENT_W - 4 * 22 * mm] + [22 * mm] * 4))
    fn = v2["interpretability"]["error_drivers_at_safety_first"]["false_negatives"]
    fp = v2["interpretability"]["error_drivers_at_safety_first"]["false_positives"]
    s.append(Spacer(1, 5))
    s.append(P("The score is transparent: each measurement has its own sub-index and the final score follows the worst one, so the app can name the driver of every result. "
               "At the safety-first threshold the calibrated score's misses come mainly from metals (%d) and BOD (%d); its false alarms mainly from pH (%d). "
               "Both kinds of error sit close to a limit, where sensor noise can tip a reading across it." % (fn["metals"], fn["bod"], fp["ph"]), BODY))

    s.append(P("Alignment with SDG 3 (Good Health and Well-being)", H2))
    for t in ("<b>Target 3.9</b> aims to reduce illness and death from hazardous chemicals and from water, air and soil pollution. AuraFlow screens for chemical and physical "
              "water-quality problems (metals, turbidity, pH, organic load) before people drink the water, and gives per-measurement treatment steps.",
              "It also supports <b>SDG 6</b> (safe drinking water, water quality) by using the same measurements and limits as BIS IS 10500, the WHO guidelines and CPCB source classes.",
              "<b>Indicators to measure in a pilot:</b> recall of unsafe samples against laboratory results (target 0.95 or more); share of flagged sources that received a confirmatory "
              "laboratory test; time from reading to alert; number of sources screened per week. No health-outcome claim is made without field data."):
        s.append(Paragraph(t, LIST, bulletText="-"))

    s.append(P("Feasibility, accessibility and deployment", H2))
    for t in ("<b>Positioning:</b> an environmental screening and triage aid, not a medical device and not a replacement for laboratory testing.",
              "<b>Accessibility:</b> runs in a phone browser with no account or install, has a larger-text option and light and dark themes, and imports CSV batches. "
              "Next steps: regional-language labels, offline use and low-bandwidth support.",
              "<b>Hardware reality:</b> pH and turbidity sensors are inexpensive, but low-cost metal sensing is weak, so metals should come from a laboratory or a validated test kit.",
              "<b>Deployment readiness:</b> health endpoint, upload and row limits, input validation, security headers and unit-tested metrics are in place. "
              "To add: rate limiting, pinned dependencies, and request-error monitoring that never logs readings."):
        s.append(Paragraph(t, LIST, bulletText="-"))

    s.append(P("Ethics and privacy", H2))
    for t in ("The server is stateless: readings live in the visitor's own browser and are sent only to be scored. There are no accounts and no database, so nothing is stored centrally.",
              "The location field is free text. Users should enter a node ID, not a home address. If storage or sharing is ever added, India's Digital Personal Data Protection Act, 2023 applies "
              "to personal data: collect the minimum, obtain consent and state the purpose.",
              "A false negative can lead someone to drink unsafe water; a false positive causes needless alarm or cost. That asymmetry is why the operating threshold is safety-first, "
              "and why every result carries the \"not tested for germs\" caveat."):
        s.append(Paragraph(t, LIST, bulletText="-"))

    s.append(P("Limits of this evidence", H2))
    for t in ("The benchmark is synthetic: ground truth comes from published limits and sensor error is simulated. Results show the pipeline works and how the score behaves; they are not clinical validation.",
              "Only pH, turbidity, metals and BOD are measured. Microbial contamination, the main cause of waterborne illness, is not covered, so a pass is never a safety guarantee.",
              "Before any real-world claim, collect laboratory-labelled field samples and report the confidence intervals."):
        s.append(Paragraph(t, LIST, bulletText="-"))
    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
