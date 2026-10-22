"""End-of-day email (spec Part D v1.5): subject, plain text and email-safe HTML, built from a chart.

Only authored text (log entries, Summary, Done, Next), never Attention. Every logbook string is escaped in the HTML.
Email-safe on purpose: table layout, bgcolor, 600 px, no JS, no SVG; the one image is cid:waypoint-mark.
Waypoint renders the email; it never sends it.
"""
import base64
import html
from datetime import date
from importlib import resources

MARK_CID = "waypoint-mark"
SIGN = "Sent at your request from your Waypoint logbook. Waypoint itself never sends mail."
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
T = {
    "canvas": "#0A0C0B", "panel": "#101312", "card": "#161A18", "line": "#242A27",
    "text": "#F2F4F3", "muted": "#8E9792", "faint": "#5E6662", "accent": "#5BE38B", "warn": "#F2B84B",
    "font": "'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif",
    "mono": "'DM Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
}
TABLE = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
e = html.escape


def mark_attachment() -> dict:
    png = resources.files("waypoint").joinpath("templates/mark.png").read_bytes()
    return {"filename": "waypoint.png", "contentType": "image/png", "contentDisposition": "inline",
            "contentId": MARK_CID, "content": base64.b64encode(png).decode("ascii")}


def report(d: dict) -> dict:
    day = date.fromtimestamp(d["generated"])
    today = day.isoformat()
    entries = {p["name"]: [x for x in p["logs"] if x["date"] == today and x["ts"] <= d["generated"]]
               for p in d["projects"]}
    included = [p for p in d["projects"] if entries[p["name"]] or p["idle"] == 0]
    others = [p["name"] for p in d["projects"]
              if p["next"] and p["status"] in ("active", "occasional") and all(p is not q for q in included)]
    counts = {"projects": len(included), "entries": sum(len(v) for v in entries.values()),
              "unlogged": sum(p["unlogged"] for p in included)}
    subject = f"Waypoint EoD — {today}"
    return {"subject": subject, "date": today, "counts": counts,
            "text": _text(subject, included, entries, others),
            "html": _html(subject, day, included, entries, others, counts)}


def _text(subject, included, entries, others):
    out = [subject, "", "PROGRESS"]
    for p in included:
        n = p["unlogged"]
        out.append(f"- {p['name']} ({p['status']})" +
                   (f": {_plural(n, 'unlogged session')} since the last log entry" if n else ""))
    out += ["", "TODAY'S WORK"]
    for p in included:
        if entries[p["name"]] or p["unlogged"]:
            out.append(p["name"])
        out += [f"- [{x['tool']}] {x['text']}" for x in entries[p["name"]]]
        if p["unlogged"]:
            out.append(f"- {_plural(p['unlogged'], 'unlogged session')}, nothing written down")
    out += ["", "COMPLETED"] + [f"- {p['name']}: {p['done']}" for p in included if p["done"]]
    out += ["", "AHEAD"] + [f"- {p['name']}: {p['next']}" for p in included if p["next"]]
    if others:
        out.append(f"Other projects with a Next: {', '.join(others)}.")
    return "\n".join(out + ["", SIGN, ""])


# ---- HTML components, adapted from the approved v1 prototype ----
# Type styles are <style> classes to keep the payload small (the agent copies html verbatim). Inline on purpose, so the
# email stays readable if a client drops <style>: bgcolor, backgrounds, cell borders, and the text colour of the panel,
# the headline and each card cell (which the entry text inherits).
CSS = f"""td,p,h1,h2{{font-family:{T['font']}}}
.m,.k,.l,.p,.ey{{font-family:{T['mono']};font-weight:500}}
.k,.l,.p{{font-size:11px;line-height:16px}}
.bt{{font-weight:700;letter-spacing:1.5px;line-height:1}}
.ey{{margin:0 0 14px;font-size:12px;line-height:16px}}
.h1{{margin:0;font-weight:800;font-size:34px;line-height:39px;letter-spacing:-1px}}
.sub{{margin:18px 0 0;font-size:16px;line-height:25px}}
.w{{color:{T['text']}}}
h2{{margin:0 0 16px;font-weight:700;font-size:19px;line-height:24px;letter-spacing:-.3px}}
.n{{font-weight:700;font-size:16px;line-height:22px}}
.q{{font-weight:600;font-size:14px;line-height:20px}}
.s{{margin:4px 0 0;font-size:13px;line-height:19px}}
.p{{display:inline-block;padding:1px 7px;border-radius:999px;white-space:nowrap}}
.k,.l{{border-top:1px solid {T['line']}}}
.k{{margin:0 0 4px;padding-top:10px}}
.x{{margin:0 0 10px;font-size:14px;line-height:21px}}
.l{{margin:0;padding-top:12px}}
.nx{{margin:4px 0 0}}
.sm{{margin-bottom:12px}}
.c{{margin:0 0 10px}}
.cp{{padding:18px 20px}}
.cq{{padding:6px 20px}}
.o{{margin:0 0 6px;font-size:14px;line-height:21px}}
.ft{{margin:14px 0 0;font-size:12px;line-height:18px}}
.ey,.l{{letter-spacing:.5px;color:{T['accent']}}}
.m,.s,.o,.sub{{color:{T['muted']}}}
.k,.ft{{color:{T['faint']}}}"""
LINE = f"border-top:1px solid {T['line']};"
INK = f' style="color:{T["text"]}"'


def _brand(size, muted=False):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="padding-right:10px;vertical-align:middle;"><img src="cid:{MARK_CID}" width="{size}" '
            f'height="{size}" alt="" style="display:block;border:0;{"opacity:.55;" if muted else ""}"></td>'
            f'<td class="bt" style="vertical-align:middle;font-size:{max(12, size - 6)}px;'
            f'color:{T["faint"] if muted else T["text"]};">WAYPOINT</td></tr></table>')


def _section(title, inner):
    return (f'<tr><td class="pad" style="padding:36px 40px 4px;">{TABLE}<tr><td style="{LINE}padding-top:30px;">'
            f'<h2>{title}</h2>{inner}</td></tr></table></td></tr>')


def _card(inner, pad="cp"):
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{T["card"]}" '
            f'class="c" style="background:{T["card"]};border:1px solid {T["line"]};"><tr>'
            f'<td class="{pad}"{INK}>{inner}</td></tr></table>')


def _pill(text, color):
    return f'<span class="p" style="color:{color};border:1px solid {color};">{e(text)}</span>'


def _entry(x):
    return f'<p class="k">{e(x["tool"])}</p><p class="x">{e(x["text"])}</p>'


def _project_card(p, today):
    pill = _pill(f"unlogged {p['unlogged']}", T["warn"]) if p["unlogged"] else _pill("logged", T["accent"])
    head = f'{TABLE}<tr><td class="n">{e(p["name"])}</td><td align="right">{pill}</td></tr></table>'
    meta = f'<p class="s sm">{e(p["summary"])}</p>' if p["summary"] else ""
    nxt = f'<p class="l">NEXT</p><p class="x nx">{e(p["next"])}</p>' if p["next"] else ""
    return _card(f'{head}{meta}{"".join(_entry(x) for x in today)}{nxt}')


def _quiet_row(i, p):
    pill = _pill(f"unlogged {p['unlogged']}", T["warn"]) if p["unlogged"] else _pill("no entry today", T["muted"])
    nxt = f'<p class="s">Next: {e(p["next"])}</p>' if p["next"] else ""
    return (f'<tr><td style="padding:12px 0;{"" if i == 0 else LINE}">{TABLE}<tr>'
            f'<td class="q">{e(p["name"])}</td><td align="right">{pill}</td></tr></table>{nxt}</td></tr>')


def _plural(n, one, many=""):
    return f"{n} {one if n == 1 else many or one + 's'}"


def _html(subject, day, included, entries, others, counts):
    logged = [p for p in included if entries[p["name"]]]
    quiet = [p for p in included if not entries[p["name"]]]
    n = counts["projects"]
    headline = f"{_plural(n, 'project')} moved today" if n else "No project moved today"
    b = '<b class="w">'
    sub = (f'You logged {b}{_plural(counts["entries"], "entry", "entries")}</b> across '
           f'{b}{_plural(len(logged), "project")}</b>.' if logged else "Nothing was logged today.")
    silent = sum(1 for p in quiet if p["unlogged"])
    if silent:
        sub += f" {_plural(silent, 'more project')} ran sessions after the last log entry, with nothing written down."
    body = (f'<tr><td class="pad" style="padding:44px 40px 8px;">'
            f'<p class="ey">END OF DAY · {day.day} {MONTHS[day.month - 1]} {day.year}</p>'
            f'<h1 class="h1"{INK}>{headline} ⚓</h1><p class="sub">{sub}</p></td></tr>')
    if logged:
        body += _section("Today's work", "".join(_project_card(p, entries[p["name"]]) for p in logged))
    if quiet:
        body += _section("Active, nothing written down",
                         _card(f'{TABLE}{"".join(_quiet_row(i, p) for i, p in enumerate(quiet))}</table>', "cq"))
    if others:
        body += _section("Also on the chart", f'<p class="o">Other projects with a Next: {e(", ".join(others))}.</p>')
    preheader = f"{headline}, {_plural(counts['entries'], 'log entry', 'log entries')}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<title>{e(subject)}</title>
<style>
{CSS}
@media (max-width:620px){{.wrap{{width:100%!important}}.pad{{padding-left:22px!important;padding-right:22px!important}}
.h1{{font-size:28px!important;line-height:33px!important}}}}
</style></head>
<body style="margin:0;padding:0;background:{T['canvas']};" bgcolor="{T['canvas']}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{T['canvas']}" style="background:{T['canvas']};">
<tr><td align="center" style="padding:32px 12px;">
<table role="presentation" class="wrap" width="600" cellpadding="0" cellspacing="0" bgcolor="{T['panel']}" style="width:600px;background:{T['panel']};border:1px solid {T['line']};color:{T['text']};">
<tr><td class="pad" style="padding:40px 40px 0;">{_brand(22)}</td></tr>
{body}
<tr><td class="pad" style="padding:8px 40px 40px;">
  {TABLE}<tr><td style="{LINE}padding-top:24px;">
  {_brand(16, muted=True)}
  <p class="ft">{SIGN}</p>
  <p class="ft" style="margin-top:10px;">Stop these: <span class="m">waypoint email --forget</span> &nbsp;·&nbsp; Change the address: <span class="m">waypoint email --to</span></p>
  </td></tr></table>
</td></tr>
</table></td></tr></table></body></html>"""
