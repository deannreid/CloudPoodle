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

.container{width:95%;max-width:1200px;margin:24px auto;background:var(--card);
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

/* tabs */
.tabs{width:95%;max-width:1200px;margin:24px auto 0 auto}
.tabbar{display:flex;flex-wrap:wrap;gap:8px;padding:0 4px}
.tabbar button{background:var(--card);color:var(--text);border:1px solid var(--border);
padding:8px 12px;border-radius:999px;cursor:pointer;font-weight:600;font-size:.9rem}
.tabbar button.active{background:linear-gradient(90deg,var(--accent2),var(--accent));
color:#fff;border-color:transparent;box-shadow:0 4px 14px rgba(0,0,0,.20)}
.tabpanel{display:none}.tabpanel.active{display:block}

/* pills (optional) */
.pill{display:inline-flex;align-items:center;justify-content:center;
padding:2px 10px;border-radius:9999px;font-weight:700;border:1px solid var(--border);
white-space:nowrap;line-height:1;font-variant-numeric:tabular-nums}
.pill.xs{padding:1px 6px;font-size:.8rem}
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

def _header_html(subtitle: str, provider: str | None) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""
  <div class="header">
    <h1>🐩 CloudPoodle Report</h1>
    {_brand_badge_html(provider)}
    <h2>{_esc(subtitle)}</h2>
    <p>Generated on {_esc(ts)}</p>
  </div>
"""

# ---------- table/section renderers ----------

def _render_table(rows: List[Dict[str, Any]], title: str) -> str:
    if not rows:
        return f"<div class='card'><h4>{_esc(title)}</h4><p>No data.</p></div>"
    cols = list(rows[0].keys())
    thead = "<tr>" + "".join(f"<th>{_esc(c)}</th>" for c in cols) + "</tr>"
    body = []
    for r in rows:
        body.append("<tr>" + "".join(f"<td>{_esc(_fmt_cell(r.get(c,'')))}</td>" for c in cols) + "</tr>")
    return f"""
    <div class="card">
      <h4>{_esc(title)}</h4>
      <div class="tablewrap">
        <table id="tbl-{_slug(title)}">
          <thead>{thead}</thead>
          <tbody>{''.join(body)}</tbody>
        </table>
      </div>
    </div>"""

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

    # Auto-render list[dict] tables for other keys
    for k, v in data_dict.items():
        if k in {"summary", "sections_html", "_inline_css", "_inline_js", "_styles", "_scripts",
                 "_container_class", "_expose"}:
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            parts.append(_render_table(v, k.title()))
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
            # Fail soft if the object isn't serializable
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
    header  = _header_html(f"Module: {module_name}", provider)
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
    header = _header_html("Multi-module report", provider)

    # Build tabs + panels; each panel gets its own CSS/JS (scoped).
    buttons, panels, first = [], [], True
    for mod_name, data in modules.items():
        if mod_name == "_meta":
            continue
        sid = _slug(mod_name)
        active = "active" if first else ""

        sec_css, sec_js, container_class, expose_snippet = _collect_module_assets(provider, data)
        summary = _summary_html((data or {}).get("summary", {}))
        details = _details_html(data or {})

        buttons.append(f'<button class="{active}" data-tab="{sid}">{_esc(mod_name)}</button>')
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
const hash=location.hash.replace('#',''); if(hash){const el=document.getElementById(hash); if(el)activate(hash);}
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
