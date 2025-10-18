# ================================================================
# File     : app_credentials_expiry.py
# Purpose  : Enumerate Entra App/Service Principal credentials and
#            identify expired / soon-to-expire secrets & certificates.
# Notes    : Adds coloured output for expiring credentials (<30 orange, <10 red)
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List
from colorama import Fore, Style

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport

REQUIRED_PERMS = ["Directory.Read.All", "Application.Read.All"]

# ================================================================
# Helper Functions
# ================================================================

def _parse_dt(val):
    if not val:
        return None
    try:
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        s = str(val).rstrip("Z")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _days_left(end):
    if not end:
        return None
    now = datetime.now(timezone.utc)
    delta = end - now
    return int(delta.days)

def _bucket(days):
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= 10:
        return "critical"
    if days <= 30:
        return "warning"
    if days <= 60:
        return "≤60d"
    if days <= 90:
        return "≤90d"
    return ">90d"

def _colour_days(days):
    """Return coloured string depending on days left."""
    if days is None:
        return "-"
    if days < 0:
        return f"{Fore.LIGHTBLACK_EX}{days}{Style.RESET_ALL}"
    if days < 10:
        return f"{Fore.RED}{days}{Style.RESET_ALL}"
    if days < 30:
        return f"{Fore.YELLOW}{days}{Style.RESET_ALL}"
    return f"{Fore.WHITE}{days}{Style.RESET_ALL}"

def _flatten_creds(obj: Dict[str, Any], obj_type: str) -> List[Dict[str, Any]]:
    rows = []
    name = obj.get("displayName") or obj.get("appDisplayName") or obj.get("appId") or obj.get("id")
    obj_id = obj.get("id"); app_id = obj.get("appId")

    for p in (obj.get("passwordCredentials") or []):
        end = _parse_dt(p.get("endDateTime")); days = _days_left(end)
        rows.append({
            "objectType": obj_type,
            "objectName": name,
            "appId": app_id or "",
            "objectId": obj_id,
            "credential": "Secret",
            "credDisplayName": p.get("displayName") or "",
            "endDate": p.get("endDateTime") or "",
            "daysRemaining": days if days is not None else "Unknown",
            "bucket": _bucket(days),
            "keyId": p.get("keyId") or "",
        })

    for k in (obj.get("keyCredentials") or []):
        end = _parse_dt(k.get("endDateTime")); days = _days_left(end)
        rows.append({
            "objectType": obj_type,
            "objectName": name,
            "appId": app_id or "",
            "objectId": obj_id,
            "credential": "Certificate",
            "credDisplayName": k.get("displayName") or "",
            "endDate": k.get("endDateTime") or "",
            "daysRemaining": days if days is not None else "Unknown",
            "bucket": _bucket(days),
            "keyId": k.get("keyId") or "",
        })
    return rows

def _colour_days_str(days):
    from colorama import Fore, Style
    if days == "Unknown" or days is None:
        return "-"
    try:
        d = int(days)
    except Exception:
        return str(days)
    if d < 0:   return f"{Fore.LIGHTBLACK_EX}{d}{Style.RESET_ALL}"
    if d < 10:  return f"{Fore.RED}{d}{Style.RESET_ALL}"
    if d < 30:  return f"{Fore.YELLOW}{d}{Style.RESET_ALL}"
    return f"{Fore.WHITE}{d}{Style.RESET_ALL}"

def _paint_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        r2 = dict(r)
        r2["daysRemaining"] = _colour_days_str(r.get("daysRemaining"))
        return_fields = ["objectName","appId","credential","credDisplayName","endDate","daysRemaining","bucket"]
        out.append({k: r2.get(k) for k in r2.keys()})  # keep full set (safe)
    return out

def _summarise(rows):
    total = len(rows)
    counts = {"expired": 0, "critical": 0, "warning": 0, "≤60d": 0, "≤90d": 0, ">90d": 0, "unknown": 0}
    for r in rows:
        bucket = r.get("bucket", "unknown")
        counts[bucket] = counts.get(bucket, 0) + 1
    return {
        "Total Credentials": total,
        "Expired": counts["expired"],
        "Critical (<10d)": counts["critical"],
        "Warning (<30d)": counts["warning"],
        "≤60 days": counts["≤60d"],
        "≤90 days": counts["≤90d"],
        ">90 days": counts[">90d"],
        "Unknown": counts["unknown"],
    }

# ================================================================
# Main Function
# ================================================================
def run(client, args):
    run_id = fncNewRunId("appcreds")
    fncPrintMessage(f"Running App Credentials Expiry (run={run_id})", "info")

    # Fetch applications
    try:
        apps = client.get_all("applications?$select=id,displayName,appId,passwordCredentials,keyCredentials")
    except Exception:
        fncPrintMessage("Retrying applications without $select (tenant schema variance).", "warn")
        apps = client.get_all("applications")

    # Fetch service principals
    try:
        sps = client.get_all("servicePrincipals?$select=id,displayName,appId,passwordCredentials,keyCredentials")
    except Exception:
        fncPrintMessage("Retrying service principals without $select (tenant schema variance).", "warn")
        sps = client.get_all("servicePrincipals")

    app_rows = []
    for a in apps:
        app_rows.extend(_flatten_creds(a, "Application"))

    sp_rows = []
    for s in sps:
        sp_rows.extend(_flatten_creds(s, "ServicePrincipal"))

    # Sort by remaining days (expired & near first)
    def _sort_key(r):
        d = r.get("daysRemaining")
        try:
            return int(d.strip(Fore.RED + Fore.YELLOW + Style.RESET_ALL))
        except Exception:
            return 9999

    app_rows.sort(key=lambda r: str(r.get("daysRemaining")))
    sp_rows.sort(key=lambda r: str(r.get("daysRemaining")))

    # Summaries
    app_summary = _summarise(app_rows)
    sp_summary = _summarise(sp_rows)

    fncPrintMessage("Applications — Credential Expiry Summary", "info")
    print(fncToTable([{"Field": k, "Value": v} for k, v in app_summary.items()], headers=["Field", "Value"], max_rows=9999))

    fncPrintMessage("Service Principals — Credential Expiry Summary", "info")
    print(fncToTable([{"Field": k, "Value": v} for k, v in sp_summary.items()], headers=["Field", "Value"], max_rows=9999))

    # Show top 20 risky creds
    def _slice(rows, n=20): return rows[:n] if len(rows) > n else rows

    if app_rows:
        fncPrintMessage("Applications — Expiring/Expired (top 25 by soonest)", "info")
        print(fncToTable(
            _paint_rows(app_rows)[:25],
            headers=["objectName","appId","credential","credDisplayName","endDate","daysRemaining","bucket"],
            max_rows=25
        ))

    if sp_rows:
        fncPrintMessage("Service Principals — Expiring/Expired (Top 20)", "info")
        print(fncToTable(_slice(sp_rows, 20),
              headers=["objectName", "appId", "credential", "credDisplayName", "endDate", "daysRemaining", "bucket"],
              max_rows=20))

    export_data = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": "aws",
        "summary": {
            "Total Credentials": app_summary["Total Credentials"] + sp_summary["Total Credentials"],
            "Expired": app_summary["Expired"] + sp_summary["Expired"],
            "Critical (<10d)": app_summary["Critical (<10d)"] + sp_summary["Critical (<10d)"],
            "Warning (<30d)": app_summary["Warning (<30d)"] + sp_summary["Warning (<30d)"],
            "≤60 days": app_summary["≤60 days"] + sp_summary["≤60 days"],
            "≤90 days": app_summary["≤90 days"] + sp_summary["≤90 days"],
            ">90 days": app_summary[">90 days"] + sp_summary[">90 days"],
            "Unknown": app_summary["Unknown"] + sp_summary["Unknown"],
        },
        "applications": app_rows,
        "servicePrincipals": sp_rows,
    }

    fncPrintMessage("App Credentials Expiry module complete.", "success")
    return export_data
