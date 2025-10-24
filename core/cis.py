# ================================================================
# File     : core/cis.py
# Purpose  : Load CIS rule packs and evaluate them against
#            CloudPoodle module payloads to produce a CIS dashboard.
# Notes    : Rules are stored at rules/cis/{provider}/level{N}.json
#            Supported ops: equals, lte, gte, all, ratio_gte,
#                           count_where, none_match
# ================================================================

from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Tuple, Optional, Callable

from core.utils import fncPrintMessage

# ------------------------- path helpers -------------------------

def _get_by_path(root: Any, path: str) -> Any:
    """
    Minimal JSON-path style:
      - Dot split for dict keys: a.b.c
      - Simple table filter: key[value=val].Field
        e.g. "role_assignments_overview[Role=Global Administrator].Assignees"
      - If any hop fails, returns None
    """
    cur = root
    if not path:
        return cur
    parts = path.split(".")
    for part in parts:
        # filter segment?  name[Key=Value]
        if "[" in part and part.endswith("]"):
            name, filt = part[:-1].split("[", 1)
            if name:
                cur = cur.get(name) if isinstance(cur, dict) else None
                if cur is None:
                    return None
            # cur should be a list
            if not isinstance(cur, list):
                return None
            key, val = filt.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            # simple equality filter on list of dicts
            matches = [r for r in cur if isinstance(r, dict) and str(r.get(key)) == val]
            # if exactly one, collapse to dict; else keep list
            cur = matches[0] if len(matches) == 1 else matches
        else:
            # normal hop
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
    return cur

def _as_number(x: Any) -> Optional[float]:
    try:
        if x is True or x is False:  # don't coerce bools to 1/0 here
            return float(x)
        if x is None:
            return None
        return float(x)
    except Exception:
        return None

def _boolish(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    return s in ("true", "1", "yes")

# ------------------------- filter helpers -------------------------

def _row_get(row: Dict[str, Any], dotted: str) -> Any:
    """Read nested keys from a single row dict using a dotted path."""
    cur: Any = row
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur

def _eval_filter_expr(row: Dict[str, Any], expr: str) -> bool:
    """
    Very small filter language:
     - Use Python operators (==, !=, >, >=, <, <=) and and/or
     - Allow dotted keys, e.g. Details.General.LastSignInDays
     - Replace tokens that look like identifiers or dotted identifiers
       with resolver calls.
     - Also support '||' -> 'or', '&&' -> 'and'
    """
    if not expr:
        return False
    src = expr.replace("&&", " and ").replace("||", " or ")

    # Tokenize very simply: split by whitespace + punctuation we care about.
    # Then replace tokens that look like identifiers/dotted identifiers with accessor.
    import re
    # An identifier or dotted identifier (Details.General.X)
    ident_re = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b")

    # Avoid replacing Python keywords/booleans/numbers
    stop = {"and", "or", "not", "True", "False", "None"}

    def repl(m):
        tok = m.group(0)
        if tok in stop:
            return tok
        # numbers should not be replaced
        if re.fullmatch(r"\d+(\.\d+)?", tok):
            return tok
        # strings will be quoted and not matched here
        return f"_get('{tok}')"

    transformed = ident_re.sub(repl, src)

    def _get(path: str) -> Any:
        return _row_get(row, path)

    try:
        return bool(eval(transformed, {"__builtins__": {}}, {"_get": _get}))
    except Exception:
        # Be conservative on parser errors
        return False

# ------------------------- test ops -------------------------

def _op_equals(actual: Any, target: Any) -> Tuple[bool, Any]:
    return (actual == target, actual)

def _op_lte(actual: Any, target: Any) -> Tuple[bool, Any]:
    a = _as_number(actual); b = _as_number(target)
    return (a is not None and b is not None and a <= b, actual)

def _op_gte(actual: Any, target: Any) -> Tuple[bool, Any]:
    a = _as_number(actual); b = _as_number(target)
    return (a is not None and b is not None and a >= b, actual)

def _op_all(ctx: Dict[str, Any], checks: List[Dict[str, Any]], modules: Dict[str, Any]) -> Tuple[bool, Any]:
    for chk in checks or []:
        # each check has path/op/value relative to 'modules'
        val = _get_by_path(modules, chk.get("path",""))
        op = chk.get("op")
        tgt = chk.get("value")
        ok, _ = _dispatch_simple(op, val, tgt)
        if not ok:
            return (False, val)
    return (True, None)

def _op_ratio_gte(modules: Dict[str, Any], num_path: str, den_path: str, threshold: float) -> Tuple[bool, Any]:
    num = _get_by_path(modules, num_path)
    den = _get_by_path(modules, den_path)
    a = _as_number(num); b = _as_number(den)
    if a is None or b in (None, 0):
        return (False, {"numerator": num, "denominator": den})
    ratio = a / b
    return (ratio >= float(threshold), {"ratio": ratio, "numerator": a, "denominator": b})

def _op_count_where(seq: Any, expr: str, compare: Dict[str, Any]) -> Tuple[bool, Any]:
    """
    seq: list[dict]
    expr: small row filter expression
    compare: {"op": "eq|lte|gte", "value": N}
    """
    if not isinstance(seq, list):
        return (False, {"error": "not_a_list"})
    n = 0
    for row in seq:
        if isinstance(row, dict) and _eval_filter_expr(row, expr):
            n += 1
    op = (compare or {}).get("op", "eq")
    tgt = (compare or {}).get("value", 0)
    if op == "eq":  ok = (n == tgt)
    elif op == "lte": ok = (n <= tgt)
    elif op == "gte": ok = (n >= tgt)
    else: ok = False
    return (ok, {"matched": n, "target": tgt, "op": op})

def _op_none_match(seq: Any, expr: str) -> Tuple[bool, Any]:
    ok, meta = _op_count_where(seq, expr, {"op": "eq", "value": 0})
    return (ok, meta)

def _dispatch_simple(op: str, actual: Any, target: Any) -> Tuple[bool, Any]:
    if op == "equals": return _op_equals(actual, target)
    if op == "lte":    return _op_lte(actual, target)
    if op == "gte":    return _op_gte(actual, target)
    return (False, actual)

# ------------------------- evaluation -------------------------

def evaluate_rules(modules_payloads: Dict[str, Any], ruleset: Dict[str, Any]) -> Dict[str, Any]:
    """
    modules_payloads: dict of {module_name: module_payload_dict}
    ruleset: parsed JSON for a level pack
    Returns dashboard-friendly dict with findings list and counters
    """
    rules = ruleset.get("rules", [])
    findings: List[Dict[str, Any]] = []

    # Flatten a combined root so paths can cross modules:
    # e.g. "user_assessment.summary.Total Users"
    root: Dict[str, Any] = {}
    for name, payload in modules_payloads.items():
        # name is the module key (e.g. "user_assessment")
        root[name] = payload

    passed = failed = 0

    for r in rules:
        rid    = r.get("id")
        title  = r.get("title")
        sev    = r.get("severity","low")
        src    = r.get("source", {}) or {}
        module = src.get("module")
        path   = src.get("path")  # optional

        status = "pass"
        actual = None
        meta   = None
        reason = r.get("pass_message","Pass")

        # Resolve primary subject (value or table to scan)
        subject = None
        if module and path:
            subject = _get_by_path(root.get(module, {}), path)
        elif module:
            subject = root.get(module)
        else:
            subject = root

        # Determine test type
        test = r.get("test", {})
        op = test.get("op")

        try:
            if op in ("equals","lte","gte"):
                ok, meta = _dispatch_simple(op, subject, test.get("value"))
                actual = subject
                status = "pass" if ok else "fail"
                reason = r.get("pass_message" if ok else "fail_message")

            elif op == "all":
                ok, meta = _op_all(test, test.get("checks"), root)
                status = "pass" if ok else "fail"
                reason = r.get("pass_message" if ok else "fail_message")

            elif op == "ratio_gte":
                ok, meta = _op_ratio_gte(
                    root,
                    test.get("numerator_path",""),
                    test.get("denominator_path",""),
                    float(test.get("value", 1.0))
                )
                status = "pass" if ok else "fail"
                reason = r.get("pass_message" if ok else "fail_message")

            elif op == "count_where":
                ok, meta = _op_count_where(subject, test.get("filter",""), test.get("compare") or {"op":"eq","value":0})
                status = "pass" if ok else "fail"
                reason = r.get("pass_message" if ok else "fail_message")

            elif op == "none_match":
                ok, meta = _op_none_match(subject, test.get("filter",""))
                status = "pass" if ok else "fail"
                reason = r.get("pass_message" if ok else "fail_message")

            else:
                status = "fail"
                reason = f"Unknown op '{op}'"
        except Exception as ex:
            status = "fail"
            reason = f"Rule error: {ex}"

        findings.append({
            "id": rid,
            "title": title,
            "severity": sev,
            "status": status,
            "reason": reason,
            "sourceModule": module or "-",
            "path": path or "-",
            "actual": actual if isinstance(actual, (str,int,float,bool)) else (meta or {}),
            "tags": r.get("tags", []),
            "remediation": r.get("remediation",""),
            "description": r.get("description",""),
            "level": r.get("level"),
        })

        passed += (1 if status == "pass" else 0)
        failed += (1 if status == "fail" else 0)

    return {
        "findings": findings,
        "counts": {"total": len(rules), "passed": passed, "failed": failed}
    }

def load_rules(provider: str, level: int) -> Dict[str, Any]:
    base = os.path.join("rules", "cis", provider.lower(), f"level{level}.json")
    if not os.path.exists(base):
        raise FileNotFoundError(f"Rule file not found: {base}")
    with open(base, "r", encoding="utf-8") as f:
        return json.load(f)
