#!/usr/bin/env python3
"""Export one or more army lists (check_list.py JSON format) to an Excel workbook.

Sheets:
  * "Comparativa" / "Comparison" — one row per list: detachments, DP, points, units,
    models, enhancements, anti-horde / anti-elite / anti-tank output, durability, OC.
  * One sheet per list — header (faction, battle size, detachments, disposition),
    a unit table (category, unit, models, points breakdown, enhancement, wargear,
    attached to, profile) with a SUM total formula, and the validation result.

Usage:
  export_xlsx.py main.json alt_1.json alt_2.json -o lista_necrones.xlsx [--lang es|en]

Each JSON may carry an optional "title" (used as the sheet name) and per-unit
"loadout" (used only for the metrics, see metrics.py).
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_list  # noqa: E402
import metrics  # noqa: E402

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

T = {
    "es": {
        "compare": "Comparativa", "list": "Lista", "faction": "Facción", "size": "Tamaño de batalla",
        "dets": "Destacamentos", "dp": "DP", "disp": "Force Disposition", "points": "Puntos", "limit": "Límite",
        "units": "Unidades", "models": "Modelos", "enh": "Mejoras", "horde": "Anti-horda", "elite": "Anti-élite",
        "tank": "Anti-tanque", "dur_h": "Aguante vs pesadas", "dur_at": "Aguante vs antitanque", "oc": "OC total",
        "legal": "Legal", "yes": "Sí", "no": "No",
        "cat": "Categoría", "unit": "Unidad", "base": "Pts base", "sur": "Recargo copia", "gear_pts": "Pts equipo",
        "enh_name": "Mejora", "enh_pts": "Pts mejora", "total": "Total", "att": "Adjunto a", "role": "Rol",
        "warlord": "Señor de la guerra", "gear": "Equipo", "profile": "Perfil (M T Sv W OC)",
        "errors": "Errores", "warnings": "Avisos", "ok": "✓ Lista legal según check_list.py",
        "data": "Datos", "metrics_note": "Daño esperado por turno contra objetivos de referencia (estimación); aguante = ataques necesarios para eliminar todo el ejército.",
        "CHARACTERS": "Personajes", "BATTLELINE": "Línea de batalla", "DEDICATED TRANSPORTS": "Transportes", "OTHER UNITS": "Otras unidades",
    },
    "en": {
        "compare": "Comparison", "list": "List", "faction": "Faction", "size": "Battle size",
        "dets": "Detachments", "dp": "DP", "disp": "Force Disposition", "points": "Points", "limit": "Limit",
        "units": "Units", "models": "Models", "enh": "Enhancements", "horde": "Anti-horde", "elite": "Anti-elite",
        "tank": "Anti-tank", "dur_h": "Durability vs heavy", "dur_at": "Durability vs anti-tank", "oc": "Total OC",
        "legal": "Legal", "yes": "Yes", "no": "No",
        "cat": "Category", "unit": "Unit", "base": "Base pts", "sur": "Copy surcharge", "gear_pts": "Wargear pts",
        "enh_name": "Enhancement", "enh_pts": "Enh. pts", "total": "Total", "att": "Attached to", "role": "Role",
        "warlord": "Warlord", "gear": "Wargear", "profile": "Profile (M T Sv W OC)",
        "errors": "Errors", "warnings": "Warnings", "ok": "✓ Legal according to check_list.py",
        "data": "Data", "metrics_note": "Expected damage per turn vs reference targets (estimate); durability = attacks needed to wipe the whole army.",
        "CHARACTERS": "Characters", "BATTLELINE": "Battleline", "DEDICATED TRANSPORTS": "Dedicated transports", "OTHER UNITS": "Other units",
    },
}

HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
HEAD_FONT = Font(bold=True, color="FFFFFF")
SUB_FILL = PatternFill("solid", fgColor="DCE6F1")
TOTAL_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="B7B7B7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def category(u):
    kw = u["keywords"]
    if "Character" in kw:
        return "CHARACTERS"
    if "Battleline" in kw:
        return "BATTLELINE"
    if "Dedicated Transport" in kw:
        return "DEDICATED TRANSPORTS"
    return "OTHER UNITS"


def safe_sheet_name(name, used):
    name = re.sub(r"[\[\]\*\?/\\:]", "-", name)[:31] or "List"
    base, i = name, 2
    while name in used:
        suffix = f" ({i})"
        name = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def header_row(ws, row, labels):
    for c, lab in enumerate(labels, 1):
        cell = ws.cell(row=row, column=c, value=lab)
        cell.fill, cell.font, cell.border = HEAD_FILL, HEAD_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def autosize(ws, min_w=8, max_w=60):
    widths = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            ln = max(len(s) for s in str(cell.value).split("\n"))
            widths[cell.column_letter] = max(widths.get(cell.column_letter, 0), ln)
    for col, w in widths.items():
        ws.column_dimensions[col].width = max(min_w, min(max_w, w + 2))


def list_sheet(wb, title, spec, res, L, used):
    d, size, pts_limit, chosen, dispositions, dp_total, dp_limit, rows, total, errors, warnings, attached, slots, enh_limit = res
    ws = wb.create_sheet(safe_sheet_name(title, used))
    disp = spec.get("force_disposition") or " / ".join(dispositions)
    info = [
        (L["list"], title),
        (L["faction"], d["faction"]),
        (L["size"], f"{size} ({pts_limit} pts)"),
        (L["dets"], ", ".join(f"{x['name']} [{x['detachment_points']} DP]" for x in chosen) + f"  —  {dp_total}/{dp_limit} DP"),
        (L["disp"], disp),
        (L["enh"], f"{slots}/{enh_limit}"),
        (L["data"], f"BSData wh40k-11e {d['data_revision']}"),
    ]
    for r, (k, v) in enumerate(info, 1):
        ws.cell(row=r, column=1, value=k).font = Font(bold=True)
        ws.cell(row=r, column=2, value=v)
    ws.cell(row=1, column=2).font = Font(bold=True, size=14)

    start = len(info) + 2
    cols = [L["cat"], L["unit"], L["models"], L["base"], L["sur"], L["gear_pts"], L["enh_name"], L["enh_pts"],
            L["total"], L["att"], L["role"], L["warlord"], L["gear"], L["profile"]]
    header_row(ws, start, cols)
    attach_of = {}
    for body_label, lst in attached.items():
        for kind, ch in lst:
            attach_of[id(ch)] = (body_label, kind)
    order = ["CHARACTERS", "BATTLELINE", "DEDICATED TRANSPORTS", "OTHER UNITS"]
    r = start + 1
    first = r
    for cat in order:
        for row in [x for x in rows if category(x["unit"]) == cat]:
            u = row["unit"]
            prof = u["profiles"][0]["stats"] if u["profiles"] else {}
            att = attach_of.get(id(row))
            vals = [L[cat], u["name"], row["models"], row["base"], row["extra"] or 0, row.get("gear_pts", 0) or 0,
                    row["enh"] or "", row["enh_pts"] or 0, None,
                    att[0] if att else "", att[1] if att else "",
                    L["yes"] if row["entry"].get("warlord") else "",
                    row["entry"].get("wargear", ""),
                    " ".join(str(prof.get(k, "")) for k in ("M", "T", "Sv", "W", "OC"))]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(row=r, column=c, value=v)
                cell.border = BORDER
                cell.alignment = Alignment(vertical="top", wrap_text=c in (13,))
            ws.cell(row=r, column=9, value=f"=D{r}+E{r}+F{r}+H{r}").font = Font(bold=True)
            r += 1
    last = r - 1
    ws.cell(row=r, column=8, value=L["total"]).font = Font(bold=True)
    tot = ws.cell(row=r, column=9, value=f"=SUM(I{first}:I{last})")
    tot.font = Font(bold=True)
    for c in range(1, len(cols) + 1):
        ws.cell(row=r, column=c).fill = TOTAL_FILL
        ws.cell(row=r, column=c).border = BORDER
    ws.cell(row=r, column=10, value=f"/ {pts_limit}")

    r += 2
    if errors:
        ws.cell(row=r, column=1, value=L["errors"]).font = Font(bold=True, color="C00000")
        for e in errors:
            r += 1
            ws.cell(row=r, column=2, value=e).font = Font(color="C00000")
        r += 1
    else:
        ws.cell(row=r, column=1, value=L["ok"]).font = Font(bold=True, color="2E7D32")
        r += 1
    if warnings:
        r += 1
        ws.cell(row=r, column=1, value=L["warnings"]).font = Font(bold=True, color="9C6500")
        for w in warnings:
            r += 1
            ws.cell(row=r, column=2, value=w)
    autosize(ws)
    ws.column_dimensions["M"].width = 40
    ws.freeze_panes = ws.cell(row=start + 1, column=3)
    return total, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lists", nargs="+")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--lang", default="es", choices=["es", "en"])
    a = ap.parse_args()
    L = T[a.lang]
    wb = Workbook()
    comp = wb.active
    comp.title = L["compare"]
    used = {comp.title}
    comp_rows = []
    for f in a.lists:
        spec = json.load(open(f, encoding="utf-8"))
        title = spec.get("title") or Path(f).stem
        res = check_list.check(spec)
        total, errors = list_sheet(wb, title, spec, res, L, used)
        d, size, pts_limit, chosen, dispositions, dp_total, dp_limit, rows, *_rest = res
        slots = res[12]
        m = metrics.list_totals(spec)
        comp_rows.append([title, ", ".join(x["name"] for x in chosen), f"{dp_total}/{dp_limit}", total, pts_limit,
                          m["units"], m["models"], slots, round(m["horde"], 1), round(m["elite"], 1), round(m["tank"], 1),
                          round(m["durability_heavy"]), round(m["durability_at"]), round(m["oc"]),
                          L["yes"] if not errors else L["no"]])
    cols = [L["list"], L["dets"], L["dp"], L["points"], L["limit"], L["units"], L["models"], L["enh"],
            L["horde"], L["elite"], L["tank"], L["dur_h"], L["dur_at"], L["oc"], L["legal"]]
    header_row(comp, 1, cols)
    for i, row in enumerate(comp_rows, 2):
        for c, v in enumerate(row, 1):
            cell = comp.cell(row=i, column=c, value=v)
            cell.border = BORDER
            if i % 2 == 0:
                cell.fill = SUB_FILL
    comp.cell(row=len(comp_rows) + 3, column=1, value=L["metrics_note"]).font = Font(italic=True, color="666666")
    autosize(comp, max_w=45)
    comp.freeze_panes = "B2"
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(out.resolve())


if __name__ == "__main__":
    main()
