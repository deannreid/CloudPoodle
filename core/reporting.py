# ================================================================
# File     : core/reporting.py
# Purpose  : Generate HTML reports (single + multi) with
#            module-injected CSS/JS and optional extra sections.
# ================================================================

import os, html, datetime, re, json
from typing import Dict, Any, List
from core.utils import fncPrintMessage
from handlers.logos import _logo_entra, _logo_aws, _logo_gcp, _logo_oracle


# ---------- tiny helpers ----------

def _esc(v: Any) -> str:
    return "" if v is None else html.escape(str(v))

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")

def _fmt_cell(val: Any) -> str:
    if isinstance(val, (dict, list)):
        try:
            s = json.dumps(val, separators=(",", ":"), ensure_ascii=False)
            if len(s) > 220:
                s = s[:200] + " … +" + str(len(s) - 200) + " chars"
            return s
        except Exception:
            return str(val)
    return "" if val is None else str(val)

def _split_camel(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", " ", str(name))
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _pretty_section_name(key: str, section_titles: Dict[str, str] | None = None) -> str:
    section_titles = section_titles or {}
    if key in section_titles:
        return section_titles[key]
    k = key
    if k.lower().endswith("kv"):
        k = k[:-2]
    k = _split_camel(k.replace("_", " "))
    return k.title() or key.title()

def _json_parse_maybe(val: Any):
    """Return (is_json, parsed_obj). Accept dict/list directly, or JSON string."""
    if isinstance(val, (dict, list)):
        return True, val
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return True, json.loads(s)
            except Exception:
                return False, None
    return False, None

def _json_summary(parsed: Any, limit: int = 180) -> str:
    """Compact one-line summary of JSON for the <summary> text."""
    try:
        compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        return (compact[:limit] + " … +" + str(len(compact) - limit) + " chars") if len(compact) > limit else compact
    except Exception:
        return str(parsed)

def _cell_html(val: Any) -> str:
    """
    Render a table cell:
      - JSON/dict/list -> <details class='cp-json'><summary>…</summary><pre>pretty</pre></details>
      - otherwise      -> escaped text
    """
    is_json, parsed = _json_parse_maybe(val)
    if is_json:
        summ = _json_summary(parsed)
        try:
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            pretty = str(parsed)
        return (
            f"<details class='cp-json'>"
            f"<summary>{_esc(summ)}</summary>"
            f"<pre>{_esc(pretty)}</pre>"
            f"</details>"
        )
    # non-JSON path
    return _esc("" if val is None else _fmt_cell(val))

# ----- bucket helpers (for colored pills) -----

def _bucket_class(bucket: str) -> str:
    """Map a bucket label to a CSS class."""
    b = (bucket or "").strip().lower()
    if b == "expired":
        return "expired"
    if b in ("critical", "crit"):
        return "crit"
    if b in ("warning", "warn"):
        return "warn"
    if b in ("≤60d", "<=60d", "≤60", "<=60", "≤90d", "<=90d", "≤90", "<=90"):
        return "soon"
    if b in (">90d", "ok"):
        return "ok"
    return "unknown"

def _bucket_from_days(days: Any) -> str:
    try:
        d = int(days)
    except Exception:
        return "unknown"
    if d < 0:
        return "expired"
    if d <= 10:
        return "critical"
    if d <= 30:
        return "warning"
    if d <= 90:
        return "≤60d" if d <= 60 else "≤90d"
    return ">90d"


# ---------- safe theme defaults (logos optional) ----------

try:
    _THEMES  # type: ignore[name-defined]
except NameError:
    _THEMES = {
        "entra":  {
            "name": "Microsoft Entra (Azure AD)",
            "accent": "#4fb3ff", "accent2": "#1f7ae0",
            "badge_bg": "#1f7ae0", "badge_fg": "#ffffff", "badge_icon": "🟦",
            "logo_b64": _logo_entra,
        },
        "aws": {
            "name": "Amazon Web Services",
            "accent": "#ffb84d", "accent2": "#ff9900",
            "badge_bg": "#ff9900", "badge_fg": "#111111", "badge_icon": "🟧",
            "logo_b64": _logo_aws,
        },
        "gcp": {
            "name": "Google Cloud",
            "accent": "#4d9fff", "accent2": "#1a73e8",
            "badge_bg": "#1a73e8", "badge_fg": "#ffffff", "badge_icon": "🟦",
            "logo_b64": _logo_gcp,
        },
        "oracle": {
            "name": "Oracle Cloud",
            "accent": "#ff6b6b", "accent2": "#d93025",
            "badge_bg": "#d93025", "badge_fg": "#ffffff", "badge_icon": "🟥",
            "logo_b64": _logo_oracle,
        },
        "other": {
            "name": "Cloud",
            "accent": "#9aa7b8", "accent2": "#6b7a8c",
            "badge_bg": "#6b7a8c", "badge_fg": "#ffffff", "badge_icon": "☁️",
            "logo_b64": "CLOUDPOODLE_LOGO_OTHER_B64",
        },
    }

def _get_theme(provider: str | None) -> Dict[str, str]:
    return _THEMES.get((provider or "entra").lower(), _THEMES["other"])

def _get_logo_data(theme: Dict[str, str]) -> tuple[str | None, str | None]:
    b64 = (theme.get("logo_b64") or "").strip()
    if not b64:
        return None, None
    mime = "image/png"
    if b64.lstrip().startswith("<svg") or b64.startswith(("PD94", "PHN2Zy")):
        mime = "image/svg+xml"
    return b64, mime

# ---------- base CSS ----------

def _base_css() -> str:
    return """
:root{
  --accent:#4fb3ff; --accent2:#1f7ae0;
  --text:#1b2330; --bg:#f5f7fb; --card:#ffffff; --border:#e3e8ef; --muted:#667085;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#0e1217; --card:#1b212a; --text:#e7edf7; --border:#2a3340; --muted:#9fb2cc; }
}
*{box-sizing:border-box} html,body{margin:0;padding:0}
body{font:15px/1.5 "Segoe UI",Roboto,Arial,system-ui;background:var(--bg);color:var(--text);}
.header{
  position:relative; background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; padding:22px 28px; border-bottom:1px solid rgba(255,255,255,.18);
  box-shadow:0 4px 14px rgba(0,0,0,.25)
}
.header h1{margin:0;font-weight:800;letter-spacing:.3px;font-size:1.9rem}
.header h2{margin:4px 0 2px 0;font-weight:500;opacity:.95}
.header p{margin:4px 0 0 0;opacity:.85;font-size:.9rem}
.header .sub{margin:0;color:#f0f4ff;opacity:.9}

.header .brand{
  position:absolute; right:20px; top:14px; display:flex; gap:8px; align-items:center;
  background:rgba(0,0,0,.10); padding:6px 10px; border-radius:999px;
  border:1px solid rgba(255,255,255,.18)
}
.brand .logo{ display:inline-flex; align-items:center; justify-content:center;
  width:26px; height:26px; border-radius:6px; background:var(--brand-bg,#1f7ae0); color:var(--brand-fg,#fff);
  font-weight:800; font-size:.9rem; }
.brand .logoimg{ width:26px; height:26px; border-radius:6px; object-fit:contain;
  background:#ffffff26; border:1px solid rgba(255,255,255,.18); padding:3px; }
.brand .name{font-weight:700; color:#fff; letter-spacing:.2px; font-size:.9rem}

.container{width:95%;max-width:1900px;margin:24px auto;background:var(--card);
border:1px solid var(--border);border-radius:12px;padding:22px 26px;box-shadow:0 10px 30px rgba(0,0,0,.20)}
h3{color:var(--accent);border-bottom:2px solid color-mix(in srgb,var(--accent) 60%, transparent);
padding-bottom:6px;margin:16px 0 8px 0;font-weight:700;letter-spacing:.2px}
.card{margin:18px 0}
.card h4{margin:0 0 8px 0;font-size:1.05rem}
.tablewrap{overflow-x:auto}

table{width:100%;border-collapse:separate;border-spacing:0;margin-top:8px;
border:1px solid var(--border);border-radius:10px;overflow:hidden;
background:color-mix(in srgb,var(--card) 92%, #000 8%)}
th,td{padding:10px 12px;border-bottom:1px solid var(--border);
word-break:break-word;overflow-wrap:anywhere;white-space:normal}
th{white-space:nowrap;background:linear-gradient(90deg,color-mix(in srgb,var(--accent2) 85%, #000 15%), var(--accent));
color:#fff;text-align:left;font-weight:700}
tr:nth-child(even) td{background:color-mix(in srgb,var(--card) 85%, #000 15%)}
tr:last-child td{border-bottom:none}
table.summary{width:min(760px,100%)}
table.summary th{width:38%;background:color-mix(in srgb,var(--accent2) 92%, #000 8%);
border-right:1px solid color-mix(in srgb,var(--accent2) 70%, #000 30%);color:#fff}
table.summary td{background:color-mix(in srgb,var(--card) 95%, #000 5%);color:var(--text)}
.footer{width:95%;max-width:1200px;margin:26px auto 12px auto;color:var(--muted);
text-align:center;font-size:.9rem}

.pill{display:inline-flex;align-items:center;justify-content:center;
padding:2px 10px;border-radius:9999px;font-weight:700;border:1px solid var(--border);
white-space:nowrap;line-height:1;font-variant-numeric:tabular-nums}
.pill.xs{padding:1px 6px;font-size:.8rem}
.pill.ok{ background:#10b98126; border:none; color:#10b981 }
.pill.warn{ background:#f59e0b26; border:none; color:#f59e0b }
.pill.crit{ background:#ef444426; border:none; color:#ef4444 }
.pill.expired{ background:#6b728026; border:none; color:#9ca3af }
.pill.soon{ background:#60a5fa26; border:none; color:#60a5fa }
.pill.unknown{ background:#64748b26; border:none; color:#94a3b8 }

td.col-days, th.col-days{ text-align:center; width:90px }
td.col-buck, th.col-buck{ text-align:center; width:110px }

.tabs{width:95%;max-width:1200px;margin:24px auto 0 auto}
.tabbar{display:flex;flex-wrap:wrap;gap:8px;padding:0 4px}
.tabbar button{background:var(--card);color:var(--text);border:1px solid var(--border);
padding:8px 12px;border-radius:999px;cursor:pointer;font-weight:600;font-size:.9rem}
.tabbar button.active{background:linear-gradient(90deg,var(--accent2),var(--accent));
color:#fff;border-color:transparent;box-shadow:0 4px 14px rgba(0,0,0,.20)}
.tabpanel{display:none}
.tabpanel.active{display:block}

/* JSON pretty dropdown */
.cp-json{ display:block }
.cp-json > summary{
  cursor:pointer; list-style:none; outline:none;
  padding:6px 10px; border:1px solid var(--border); border-radius:8px;
  background: color-mix(in srgb, var(--card) 96%, #000 4%);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size:.9rem; line-height:1.4; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.cp-json > summary::-webkit-details-marker{ display:none }
.cp-json[open] > summary{ border-bottom-left-radius:0; border-bottom-right-radius:0 }
.cp-json pre{
  margin:0; padding:12px 14px; border:1px solid var(--border); border-top:none;
  border-bottom-left-radius:8px; border-bottom-right-radius:8px;
  background: color-mix(in srgb, var(--card) 94%, #000 6%);
  max-height:360px; overflow:auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size:.9rem; line-height:1.4;
}


"""

def _theme_css(provider: str | None) -> str:
    t = _get_theme(provider)
    return f"""
:root{{ --accent:{t['accent']}; --accent2:{t['accent2']}; }}
.header .brand .logo{{ background:{t['badge_bg']}; color:{t['badge_fg']}; }}
"""

# ---------- header & small utils ----------

def _brand_badge_html(provider: str | None) -> str:
    t = _get_theme(provider)
    label = _esc((provider or "entra").upper())
    b64, mime = _get_logo_data(t)
    if b64:
        return (
            f'<div class="brand" title="{_esc(t["name"])}">'
            f'<img class="logoimg" alt="{label} logo" src="data:{mime};base64,{b64}" />'
            f'<span class="name">{label}</span></div>'
        )
    return f'<div class="brand" title="{_esc(t["name"])}"><span class="logo">🐩</span><span class="name">{label}</span></div>'

def _header_html(title: str, provider: str | None, subtitle_small: str | None = None) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sub_small = f'<p class="sub">{_esc(subtitle_small)}</p>' if subtitle_small else ""
    return f"""
  <div class="header">
    <h1>🐩 CloudPoodle Report</h1>
    {_brand_badge_html(provider)}
    <h2>{_esc(title)}</h2>
    {sub_small}
    <p>Generated on {_esc(ts)}</p>
  </div>
"""

# ---------- table/section renderers ----------

def _render_table(rows: List[Dict[str, Any]], title: str) -> str:
    if not rows:
        return f"<div class='card'><h4>{_esc(title)}</h4><p>No data.</p></div>"
    cols = list(rows[0].keys())

    # header with special classes for days/bucket
    head_cells = []
    for c in cols:
        if c == "daysRemaining":
            head_cells.append("<th class='col-days'>daysRemaining</th>")
        elif c == "bucket":
            head_cells.append("<th class='col-buck'>bucket</th>")
        else:
            head_cells.append(f"<th>{_esc(c)}</th>")
    thead = "<tr>" + "".join(head_cells) + "</tr>"

    body = []
    for r in rows:
        tds = []
        row_bucket = r.get("bucket")
        for c in cols:
            raw = r.get(c, "")
            if c == "bucket":
                cls = _bucket_class(str(raw))
                tds.append(f"<td class='col-buck'><span class='pill xs {cls}'>{_esc(raw)}</span></td>")
            elif c == "daysRemaining":
                cls = _bucket_class(row_bucket) if row_bucket else _bucket_class(_bucket_from_days(raw))
                tds.append(f"<td class='col-days'><span class='pill xs {cls}'>{_esc(raw)}</span></td>")
            else:
                tds.append(f"<td>{_esc(_fmt_cell(raw))}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    
    body_rows = []
    for r in rows:
        tds = []
        for c in cols:
            raw = r.get(c, "")
            # Render with JSON-friendly dropdown when applicable
            tds.append(f"<td>{_cell_html(raw)}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    return f"""
    <div class="card">
      <h4>{_esc(title)}</h4>
      <div class="tablewrap">
        <table id="tbl-{_slug(title)}">
          <thead>{thead}</thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
      </div>
    </div>
    """

def _render_sections_html(sections: List[Dict[str, str]]) -> str:
    out = []
    for s in sections or []:
        title = _esc(s.get("title",""))
        html_block = s.get("html","")
        out.append(f"<div class='card'><h4>{title}</h4>{html_block}</div>")
    return "".join(out)

def _summary_html(summary: Dict[str, Any]) -> str:
    if not summary:
        return "<p>No summary data available.</p>"
    rows = "\n".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in summary.items())
    return f"<table class='summary'>{rows}</table>"

def _details_html(data_dict: Dict[str, Any]) -> str:
    parts = []
    # Optional “HTML sections”
    if isinstance(data_dict.get("sections_html"), list):
        parts.append(_render_sections_html(data_dict["sections_html"]))

    section_titles = data_dict.get("_section_titles") or {}

    # Auto-render list[dict] tables for other keys
    for k, v in data_dict.items():
        if k in {"summary", "sections_html", "_inline_css", "_inline_js", "_styles", "_scripts",
                 "_container_class", "_expose", "_title", "_subtitle", "_section_titles"}:
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            parts.append(_render_table(v, _pretty_section_name(k, section_titles)))
    return "\n".join(parts)

# ---------- asset collectors (module can inject CSS/JS) ----------

def _collect_module_assets(provider: str | None, data: Dict[str, Any]) -> tuple[str, str, str, str]:
    """
    Return (full_css, full_js, container_class, exposed_json_script).
    - _inline_css   : str   (single)
    - _styles       : list[str] (additional style blocks)
    - _inline_js    : str   (single)
    - _scripts      : list[str] (additional script blocks)
    - _expose       : any JSON-serializable object exposed as window._cp
    - _container_class : str
    """
    base = _base_css() + _theme_css(provider)

    # CSS
    css_blocks = []
    if isinstance(data.get("_styles"), list):
        css_blocks.extend([c for c in data["_styles"] if isinstance(c, str)])
    if isinstance(data.get("_inline_css"), str):
        css_blocks.append(data["_inline_css"])
    full_css = base + "".join(css_blocks)

    # JS
    js_blocks = []
    if isinstance(data.get("_scripts"), list):
        js_blocks.extend([s for s in data["_scripts"] if isinstance(s, str)])
    if isinstance(data.get("_inline_js"), str):
        js_blocks.append(data["_inline_js"])
    full_js = "".join(js_blocks)

    # Optional “expose” object → window._cp
    exposed = data.get("_expose")
    expose_script = ""
    if exposed is not None:
        try:
            expose_script = f"<script>window._cp = {json.dumps(exposed, ensure_ascii=False)};</script>"
        except Exception:
            expose_script = "<script>window._cp = {};</script>"

    container = data.get("_container_class") or ""
    return full_css, full_js, container, expose_script

# ================================================================
# Single-module report
# ================================================================
def fncWriteHTMLReport(filename: str, module_name: str, data_dict: Dict[str, Any]) -> None:
    fncPrintMessage(f"Generating HTML report: {filename}", "info")
    provider = (data_dict or {}).get("provider", "entra")

    css, js, container_class, expose_snippet = _collect_module_assets(provider, data_dict)

    # Allow modules to set the header lines
    page_h2      = data_dict.get("_title") or f"Module: {module_name}"
    page_subline = data_dict.get("_subtitle")

    header  = _header_html(page_h2, provider, page_subline)
    summary = _summary_html(data_dict.get("summary", {}))
    details = _details_html(data_dict)

    html_doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>CloudPoodle Report - {_esc(module_name)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>{css}</style></head><body>
{header}
<div class="container{(' ' + _esc(container_class)) if container_class else ''}">
  <h3>Summary</h3>
  {summary}
  {details}
</div>
<div class="footer">
  <p>Generated by <b>CloudPoodle</b> 🐩 — "Because every cloud deserves a good sniff."</p>
  <p>&copy; {datetime.datetime.now(datetime.timezone.utc).year} CloudPoodle Framework</p>
</div>
{expose_snippet}
{f"<script>{js}</script>" if js else ""}
</body></html>"""

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_doc)
    fncPrintMessage(f"HTML report written to {filename}", "success")

# ================================================================
# Multi-module report
# ================================================================
def fncWriteHTMLReportMulti(filename: str, modules: Dict[str, Dict[str, Any]]) -> None:
    fncPrintMessage(f"Generating multi-module HTML report: {filename}", "info")

    # infer provider from meta or first module that has it
    provider = None
    if isinstance(modules.get("_meta"), dict):
        provider = modules["_meta"].get("provider")
    if not provider:
        for _, v in modules.items():
            if isinstance(v, dict) and "provider" in v:
                provider = v["provider"]; break

    base_css = _base_css() + _theme_css(provider)

    # Header lines from _meta if provided
    meta = modules.get("_meta") or {}
    page_h2      = meta.get("_title") or "Multi-module report"
    page_subline = meta.get("_subtitle")

    header = _header_html(page_h2, provider, page_subline)

    buttons, panels, first = [], [], True
    for mod_name, data in modules.items():
        if mod_name == "_meta":
            continue
        sid = _slug(mod_name)
        active = "active" if first else ""

        # Nicer button label: prefer _tab_title > _title > prettified key
        label = (data or {}).get("_tab_title") or (data or {}).get("_title") \
                or _split_camel(mod_name.replace("_"," ")).title()

        sec_css, sec_js, container_class, expose_snippet = _collect_module_assets(provider, data)
        summary = _summary_html((data or {}).get("summary", {}))
        details = _details_html(data or {})

        buttons.append(f'<button class="{active}" data-tab="{sid}">{_esc(label)}</button>')
        panels.append(f"""
<section id="{sid}" class="tabpanel {active}">
  <style>{sec_css}</style>
  <div class="container{(' ' + _esc(container_class)) if container_class else ''}">
    <h3>Summary</h3>
    {summary}
    {details}
  </div>
  {expose_snippet}
  {f"<script>{sec_js}</script>" if sec_js else ""}
</section>""")
        first = False

    tabs_html = f"""
<div class="tabs">
  <div class="tabbar">{''.join(buttons)}</div>
</div>
{''.join(panels)}
"""

    tabs_js = """
<script>
const buttons=[...document.querySelectorAll('.tabbar button')];
const panels=[...document.querySelectorAll('.tabpanel')];
function activate(id){
  buttons.forEach(b=>b.classList.toggle('active',b.dataset.tab===id));
  panels.forEach(p=>p.classList.toggle('active',p.id===id));
  history.replaceState(null,'','#'+id);
}
buttons.forEach(b=>b.addEventListener('click',()=>activate(b.dataset.tab)));
const hash=location.hash.replace('#','');
if(hash){
  const el=document.getElementById(hash);
  if(el){ activate(hash); }
}else{
  // ensure only the first is visible on load
  const firstPanel=panels[0]; const firstBtn=buttons[0];
  if(firstPanel){ panels.forEach(p=>p.classList.remove('active')); firstPanel.classList.add('active'); }
  if(firstBtn){ buttons.forEach(b=>b.classList.remove('active')); firstBtn.classList.add('active'); }
}
</script>
"""

    html_doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>CloudPoodle Report — Multi</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>{base_css}</style></head><body>
{header}
{tabs_html}
<div class="footer">
  <p>Generated by <b>CloudPoodle</b> 🐩 — "Because every cloud deserves a good sniff."</p>
  <p>&copy; {datetime.datetime.now(datetime.timezone.utc).year} CloudPoodle Framework</p>
</div>
{tabs_js}
</body></html>"""

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_doc)
    fncPrintMessage(f"HTML report written to {filename}", "success")
