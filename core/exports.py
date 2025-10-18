# ================================================================
# File     : exports.py
# Purpose  : Handle all export logic for CloudPoodle (HTML, CSV, JSON)
# Notes    : Called by CloudPoodle.py after module(s) finish
# ================================================================

import pathlib
from datetime import datetime, timezone

from core.utils import fncPrintMessage, fncEnsureFolder, fncExportCSV, fncWriteJSON
from core.reporting import fncWriteHTMLReport, fncWriteHTMLReportMulti


# ================================================================
# Function: fncExportList
# Purpose  : Flatten --export list-of-lists from argparse
# ================================================================
def fncExportList(args_export) -> set:
    if not args_export:
        return set()
    out = set()
    for chunk in args_export:
        if isinstance(chunk, (list, tuple)):
            for item in chunk:
                if isinstance(item, str):
                    for part in item.replace(",", " ").split():
                        if part.strip():
                            out.add(part.strip().lower())
        elif isinstance(chunk, str):
            for part in chunk.replace(",", " ").split():
                if part.strip():
                    out.add(part.strip().lower())
    return out


# ================================================================
# Function: fncGetExportPath
# Purpose  : Build structured output path under ~/.cloudpoodle/reports/
# ================================================================
def fncGetExportPath(module_name: str, root: pathlib.Path = None):
    if root is None:
        root = pathlib.Path.home() / ".cloudpoodle" / "reports"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mod_slug = module_name.replace("/", "_").replace("\\", "_")
    out_dir = root / ts / mod_slug
    fncEnsureFolder(out_dir)
    return out_dir


# ================================================================
# Function: fncExportSingleModule
# Purpose  : Handle all export formats for one module
# ================================================================
def fncExportSingleModule(module_name: str, data: dict, formats: set, root: pathlib.Path):
    out_dir = fncGetExportPath(module_name, root)

    if "json" in formats:
        fncWriteJSON(str(out_dir / f"{module_name}.json"), data)

    if "csv" in formats:
        for key, val in data.items():
            if key == "summary":
                continue
            if isinstance(val, list) and val and isinstance(val[0], dict):
                fncExportCSV(str(out_dir / f"{module_name}_{key}.csv"), val)

    if "html" in formats:
        fncWriteHTMLReport(str(out_dir / f"{module_name}.html"), module_name, data)

    fncPrintMessage(f"Exports written → {out_dir}", "success")


# ================================================================
# Function: fncExportMultiModule
# Purpose  : Handle all export formats when running multiple modules
# ================================================================
def fncExportMultiModule(results: dict, formats: set, root: pathlib.Path):
    out_dir = fncGetExportPath("ALL_MODULES", root)

    if "json" in formats:
        fncWriteJSON(str(out_dir / "all_modules.json"), results)

    if "csv" in formats:
        for mod, data in results.items():
            mod_dir = out_dir / mod
            fncEnsureFolder(mod_dir)
            for key, val in (data or {}).items():
                if key == "summary":
                    continue
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    fncExportCSV(str(mod_dir / f"{mod}_{key}.csv"), val)

    if "html" in formats:
        fncWriteHTMLReportMulti(str(out_dir / "CloudPoodle_Report.html"), results)

    fncPrintMessage(f"Exports written → {out_dir}", "success")
