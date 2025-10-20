# ================================================================
# File     : core/reporting.py
# Purpose  : Generate HTML reports (single + multi) with
#            module-injected CSS/JS and optional extra sections.
#            Now also supports a dashboard "cards" layout with KPIs,
#            Standouts, and Charts (Chart.js).
# ================================================================

import os, html, datetime, re, json
from typing import Dict, Any, List, Tuple
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

def _json_parse_maybe(val: Any) -> Tuple[bool, Any]:
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

def _bucket_class(bucket: Any) -> str:
    """Map a bucket label/value to a CSS class."""
    b = str(bucket or "").strip().lower()
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

/* ======== Dashboard cards layout ======== */
.grid{display:grid; gap:12px}
.grid.kpis{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.grid.three{grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.card-rounded{border-radius:12px; box-shadow:0 6px 18px rgba(0,0,0,.08); border:1px solid var(--border)}
.kpi{padding:14px 16px}
.kpi .top{display:flex; align-items:center; justify-content:space-between}
.kpi .label{color:var(--muted); font-weight:600}
.kpi .badge{border-radius:999px; padding:4px 10px; font-weight:700; font-size:.8rem; border:1px solid var(--border)}
.kpi .value{font-size:1.8rem; font-weight:800; margin-top:4px}
.kpi .delta{font-size:.9rem; opacity:.8}
.badge.primary{background:#1f7ae0; color:#fff; border-color:transparent}
.badge.success{background:#10b981; color:#fff; border-color:transparent}
.badge.warning{background:#f59e0b; color:#111; border-color:transparent}
.badge.danger{background:#ef4444; color:#fff; border-color:transparent}
.badge.info{background:#0ea5e9; color:#fff; border-color:transparent}
.badge.secondary{background:#6b7280; color:#fff; border-color:transparent}
.badge.dark{background:#111827; color:#fff; border-color:transparent}

.standout .title{font-weight:700; display:flex; align-items:center; gap:8px}
.standout .score{font-size:1.4rem; font-weight:800; color:#ef4444}
.standout .meta{color:var(--muted); font-size:.9rem}
.charts{display:grid; grid-template-columns: 1.5fr 1fr; gap:12px}
@media (max-width: 1000px){ .charts{grid-template-columns:1fr} }

/* ===== Summary + Chart side-by-side ===== */
.summary-grid{display:grid;grid-template-columns:1fr minmax(260px,420px);gap:12px;align-items:start}
@media (max-width: 1100px){ .summary-grid{grid-template-columns:1fr} }
.summary-chart.card-rounded{padding:10px 12px}
.summary-chart .title{font-weight:700;margin-bottom:6px}
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

# ---------- dashboard renderers (new) ----------

def _render_kpis(kpis: List[Dict[str, Any]]) -> str:
    if not kpis: return ""
    blocks = []
    for k in kpis:
        label = _esc(k.get("label",""))
        value = _esc(k.get("value",""))
        delta = _esc(k.get("delta",""))
        tone  = _esc(k.get("tone","primary")) or "primary"
        icon  = _esc(k.get("icon",""))
        icon_html = f'<span class="bi {icon}" style="margin-right:6px"></span>' if icon else ""
        delta_html = f'<div class="delta">{delta}</div>' if delta else ""
        blocks.append(f"""
        <div class="card-rounded kpi">
          <div class="top">
            <div class="label">{label}</div>
            <span class="badge {tone}">{icon_html}{tone.title()}</span>
          </div>
          <div class="value">{value}</div>
          {delta_html}
        </div>""")
    return f'<div class="grid kpis">{"".join(blocks)}</div>'

def _render_standouts(standouts: Dict[str, Dict[str, Any]] | None) -> str:
    s = standouts or {}
    tiles = []
    order = [("group","Highest Risk Group"), ("user","Highest Risk User"), ("computer","Highest Risk Computer")]
    for key, fallback_title in order:
        item = s.get(key)
        if not item:
            tiles.append(f"""
            <div class="card-rounded standout" style="padding:14px 16px">
              <div class="title">⭐ {fallback_title}</div>
              <div class="meta">No data</div>
            </div>""")
            continue
        title = _esc(item.get("title") or fallback_title)
        name  = _esc(item.get("name",""))
        score = item.get("risk_score", 0)
        comment = _esc(item.get("comment",""))
        tiles.append(f"""
        <div class="card-rounded standout" style="padding:14px 16px">
          <div class="title">⭐ {title}</div>
          <div class="dflex" style="display:flex; align-items:center; justify-content:space-between; gap:8px">
            <div>
              <div class="fw" style="font-weight:700">{name}</div>
              <div class="meta">{comment}</div>
            </div>
            <div class="score">{float(score):.2f}</div>
          </div>
        </div>""")
    return f'<div class="grid three">{"".join(tiles)}</div>'

def _render_charts(charts: Dict[str, Any] | None) -> Tuple[str, str, bool]:
    """
    Returns (html, js, needs_chartjs)
    charts = {
      "trend": {"labels":[...], "series":[{"label":"Findings","data":[...]}, ...]},
      "severity": {"labels":[...], "data":[...] }
    }
    """
    if not charts: return "", "", False
    html_parts, js_parts = [], []
    needs = False

    # Trend line / area
    if isinstance(charts.get("trend"), dict):
        cid = f"chart-{os.urandom(4).hex()}"
        t = charts["trend"]
        labels = json.dumps(t.get("labels", []))
        series = json.dumps([{"label": s.get("label","Series"), "data": s.get("data", [])} for s in t.get("series", [])])
        html_parts.append(f"""
        <div class="card-rounded" style="padding:12px 14px">
          <div class="title" style="font-weight:700;margin-bottom:6px">Findings Over Time</div>
          <canvas id="{cid}"></canvas>
        </div>""")
        js_parts.append(f"""
        (()=>{{
          const ctx=document.getElementById("{cid}");
          const labels={labels};
          const datasets={series}.map(s=>({{label:s.label,data:s.data,fill:true,tension:.35}}));
          new Chart(ctx,{{type:'line',data:{{labels,datasets}},options:{{plugins:{{legend:{{position:'bottom'}}}},scales:{{y:{{beginAtZero:true}}}}}}}});
        }})();""")
        needs = True

    # Severity doughnut
    if isinstance(charts.get("severity"), dict):
        cid = f"chart-{os.urandom(4).hex()}"
        s = charts["severity"]
        labels = json.dumps(s.get("labels", []))
        data   = json.dumps(s.get("data", []))
        html_parts.append(f"""
        <div class="card-rounded" style="padding:12px 14px">
          <div class="title" style="font-weight:700;margin-bottom:6px">Severity Breakdown</div>
          <canvas id="{cid}"></canvas>
        </div>""")
        js_parts.append(f"""
        (()=>{{
          const ctx=document.getElementById("{cid}");
          new Chart(ctx,{{type:'doughnut',data:{{labels:{labels},datasets:[{{data:{data}}}]}},options:{{plugins:{{legend:{{position:'bottom'}}}}}}}});
        }})();""")
        needs = True

    if html_parts:
        html = f'<div class="charts">{"".join(html_parts)}</div>'
    else:
        html = ""
    return html, "\n".join(js_parts), needs

def _summary_with_severity(summary: Dict[str, Any], severity_chart: Dict[str, Any]) -> tuple[str, str, bool]:
    """
    Render summary table next to a compact severity doughnut.
    Returns (html, js, needs_chartjs).
    """
    if not summary:
        summary_html = "<p>No summary data available.</p>"
    else:
        rows = "\n".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in summary.items())
        summary_html = f"<table class='summary'>{rows}</table>"

    if not severity_chart:
        return summary_html, "", False

    cid = f"chart-{os.urandom(4).hex()}"
    labels = json.dumps(severity_chart.get("labels", []))
    data   = json.dumps(severity_chart.get("data", []))
    chart_html = f"""
    <div class="summary-chart card-rounded">
      <div class="title">Severity Breakdown</div>
      <canvas id="{cid}" height="220"></canvas>
    </div>
    """
    js = f"""
    (()=>{{
      const ctx=document.getElementById("{cid}");
      new Chart(ctx,{{type:'doughnut',data:{{labels:{labels},datasets:[{{data:{data}}}]}},
        options:{{plugins:{{legend:{{position:'bottom'}}}}}}}});
    }})();"""
    html = f"<div class='summary-grid'><div>{summary_html}</div>{chart_html}</div>"
    return html, js, True


def _dashboard_html(data_dict: Dict[str, Any]) -> Tuple[str, str, bool]:
    """
    Returns (dashboard_html, dashboard_js, needs_chartjs)
    Renders KPI cards, Standouts, and Charts if provided.
    """
    kpis = data_dict.get("_kpis") or []
    standouts = data_dict.get("_standouts") or {}
    charts = data_dict.get("_charts") or {}

    pieces = []
    if kpis:       pieces.append(_render_kpis(kpis))
    if standouts:  pieces.append(_render_standouts(standouts))
    ch_html, ch_js, needs = _render_charts(charts)
    if ch_html:    pieces.append(ch_html)

    return "\n".join(pieces), ch_js, needs


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

    body_rows = []
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
                 "_container_class", "_expose", "_title", "_subtitle", "_section_titles",
                 "_kpis", "_standouts", "_charts"}:
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

    # Optional dashboard section (boxes + charts)
    dash_html, dash_js, needs_chartjs = _dashboard_html(data_dict)

    # Handle chart placement: if _charts.place == "summary" (or _charts_place == "summary"),
    # render the severity doughnut next to the summary table; otherwise keep old layout.
    charts_spec = data_dict.get("_charts") or {}
    place_summary = (charts_spec.get("place") == "summary") or (data_dict.get("_charts_place") == "summary")

    summary_block_js = ""
    needs_chartjs_summary = False
    summary_data = data_dict.get("summary", {})
    severity_spec = charts_spec.get("severity") if place_summary else None

    if place_summary and severity_spec:
        summary, sum_js, needs_chartjs_summary = _summary_with_severity(summary_data, severity_spec)
        summary_block_js = sum_js
        # prevent dashboard from rendering this same chart again
        charts_spec = dict(charts_spec)
        charts_spec.pop("severity", None)
    else:
        summary = _summary_html(summary_data)

    # Build dashboard (now with possibly-trimmed charts_spec)
    tmp = dict(data_dict)
    tmp["_charts"] = charts_spec
    dash_html, dash_js, needs_chartjs_dash = _dashboard_html(tmp)
    needs_chartjs = needs_chartjs_summary or needs_chartjs_dash
    details = _details_html(data_dict)

    chartjs_tag = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>' if needs_chartjs else ""

    html_doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>CloudPoodle Report - {_esc(module_name)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>{css}</style></head><body>
{header}
<div class="container{(' ' + _esc(container_class)) if container_class else ''}">
  {dash_html}
  <h3>Summary</h3>
  {summary}
  {details}
</div>
<div class="footer">
  <p>Generated by <b>CloudPoodle</b> 🐩 — "Because every cloud deserves a good sniff."</p>
  <p>&copy; {datetime.datetime.now(datetime.timezone.utc).year} CloudPoodle Framework</p>
</div>
{expose_snippet}
{chartjs_tag}
{f"<script>{summary_block_js}</script>" if summary_block_js else ""}
{f"<script>{dash_js}</script>" if dash_js else ""}
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

        sec_css, sec_js, container_class, expose_snippet = _collect_module_assets(provider, data or {})
        dash_html, dash_js, needs_chartjs = _dashboard_html(data or {})
        charts_spec = (data or {}).get("_charts") or {}
        place_summary = (charts_spec.get("place") == "summary") or ((data or {}).get("_charts_place") == "summary")

        summary_block_js = ""
        needs_chartjs_summary = False
        summary_data = (data or {}).get("summary", {})
        severity_spec = charts_spec.get("severity") if place_summary else None

        if place_summary and severity_spec:
            summary, sum_js, needs_chartjs_summary = _summary_with_severity(summary_data, severity_spec)
            summary_block_js = sum_js
            charts_spec = dict(charts_spec); charts_spec.pop("severity", None)
        else:
            summary = _summary_html(summary_data)

        tmp = dict(data or {}); tmp["_charts"] = charts_spec
        dash_html, dash_js, needs_chartjs_dash = _dashboard_html(tmp)
        needs_chartjs = needs_chartjs_summary or needs_chartjs_dash

        details = _details_html(data or {})

        chartjs_tag = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>' if needs_chartjs else ""

        buttons.append(f'<button class="{active}" data-tab="{sid}">{_esc(label)}</button>')
        panels.append(f"""
<section id="{sid}" class="tabpanel {active}">
  <style>{sec_css}</style>
  <div class="container{(' ' + _esc(container_class)) if container_class else ''}">
    {dash_html}
    <h3>Summary</h3>
    {summary}
    {details}
  </div>
    {chartjs_tag}
    {f"<script>{summary_block_js}</script>" if summary_block_js else ""}
    {f"<script>{dash_js}</script>" if dash_js else ""}
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
