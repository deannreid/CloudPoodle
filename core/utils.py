# ================================================================
# File     : utils.py
# Purpose  : Common helpers for CloudPoodle (console, files, time, data)
# Notes    : British English; witty output; safe imports
# ================================================================

import os
import json
import csv
import time
import uuid
import pathlib
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEBUG_ENABLED = False

# Optional deps: only import if present
try:
    from colorama import Fore, Style, init as _colorama_init
    _colorama_init(autoreset=True)
except Exception:  # pragma: no cover
    class _Dummy:
        RESET_ALL = ""
    class _Fore(_Dummy):
        CYAN = YELLOW = RED = GREEN = MAGENTA = WHITE = ""
    class _Style(_Dummy): ...
    Fore, Style = _Fore(), _Dummy()

try:
    from tabulate import tabulate
except Exception:  # pragma: no cover
    def tabulate(rows, headers="firstrow", tablefmt="github"):
        # Fallback: simple plain text
        if headers and isinstance(headers, (list, tuple)):
            header_line = " | ".join(map(str, headers))
            sep = "-+-".join("-" * len(str(h)) for h in headers)
            body = "\n".join(" | ".join(map(str, r)) for r in rows)
            return f"{header_line}\n{sep}\n{body}"
        return "\n".join(" | ".join(map(str, r)) for r in rows)

# ================================================================
# Function: fncSetDebug
# Purpose : Globally enable/disable debug output
# Notes   : Called from main after parsing --debug
# ================================================================
def fncSetDebug(enabled: bool) -> None:
    global DEBUG_ENABLED
    DEBUG_ENABLED = bool(enabled)


# ================================================================
# Function: fncPrintMessage
# Purpose : Standardised console output with levels and colours
# Notes   : Levels: info, warn, error, success, debug; witty by design
# ================================================================
def fncPrintMessage(message: str, level: str = "info") -> None:
    if level == "debug" and not DEBUG_ENABLED:
        return
    colours = {
        "info": Fore.CYAN,
        "warn": Fore.YELLOW,
        "error": Fore.RED,
        "success": Fore.GREEN,
        "debug": Fore.MAGENTA
    }
    prefix = {
        "info": "[•]",
        "warn": "[!]",
        "error": "[✗]",
        "success": "[✓]",
        "debug": "[∆]"
    }
    colour = colours.get(level, "")
    mark = prefix.get(level, "[ ]")
    print(f"{colour}{mark} {message}{Style.RESET_ALL}")


# ================================================================
# Function: fncDisplayBanner
# Purpose : Display CloudPoodle ASCII banner in rainbow colours
# Notes   : Cycles through colour palette; resets at line breaks
# ================================================================
# ================================================================
# Function: fncDisplayBanner
# Purpose : Display CloudPoodle ASCII banner in rainbow colours
# Notes   : Includes Poodle mascot aligned to the right of the banner 🐩
# ================================================================
def fncDisplayBanner(version: str = "v1.0"):
    from colorama import Fore, Style

    # Main title ASCII
    banner_lines = [
        "_________ .__                   ._____________                 .___.__          ",
        "\\_   ___ \\|  |   ____  __ __  __| _/\\______   \\____   ____   __| _/|  |   ____  ",
        "/    \\  \\/|  |  /  _ \\|  |  \\/ __ |  |     ___/  _ \\ /  _ \\ / __ | |  | _/ __ \\ ",
        "\\     \\___|  |_(  <_> )  |  / /_/ |  |    |  (  <_> |  <_> ) /_/ | |  |_\\  ___/ ",
        " \\______  /____/\\____/|____/\\____ |  |____|   \\____/ \\____/\\____ | |____/\\___  >",
        "        \\/                       \\/                             \\/           \\/  ",
    ]

    # Cute CloudPoodle mascot
    poodle_lines = [
        "   _     /)---(\\   ",
        "   \\   (/ . . \\)  ",
        "    \\__)-\\(*)/    ",
        "     \\_       (_   ",
        "     (___/-(____)   "
    ]

    colours = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.BLUE]

    def rainbow(text: str) -> str:
        """Cycle through colours for a rainbow effect"""
        out = ""
        for i, ch in enumerate(text):
            out += colours[i % len(colours)] + ch
        return out + Style.RESET_ALL

    print("\n")

    # Combine main banner and poodle side by side
    max_banner_len = max(len(line) for line in banner_lines)
    combined_lines = []
    for i in range(max(len(banner_lines), len(poodle_lines))):
        banner_part = banner_lines[i] if i < len(banner_lines) else ""
        poodle_part = poodle_lines[i - len(banner_lines)] if i >= len(banner_lines) else ""
        # pad banner to align the poodle nicely
        combined_line = banner_part.ljust(max_banner_len + 5)
        if i < len(poodle_lines):
            combined_line += poodle_lines[i]
        combined_lines.append(combined_line)

    # Print combined lines with rainbow gradient
    for line in combined_lines:
        print(rainbow(line))

    print(f"{Fore.CYAN}\nCloudPoodle {version} — 'Because every cloud deserves a good sniff.'{Style.RESET_ALL}\n")

# ================================================================
# Function: fncBlurb
# Purpose : Display a witty blurb describing current action
# Notes   : Ideal for transitions like module loading or scan start
# ================================================================
def fncBlurb(action: str, flavour: str = None):
    blurbs = {
        "entra": [
            "Sniffing Entra configurations for stale biscuits…",
            "Unleashing the Poodle on your Entra tenant…",
            "Following the Graph scent trail into Azure AD…"
        ],
        "aws": [
            "Barking up the S3 tree…",
            "Poking IAM policies with a very British paw…",
            "Sniffing around AWS credentials…"
        ],
        "gcp": [
            "Checking GCP buckets for loose bones…",
            "Digging through service accounts…",
            "Sniffing for misconfigured clouds over Googleland…"
        ],
        "generic": [
            "Preparing the harness…",
            "Sharpening claws and sniffers…",
            "Warming up the cloud engines…"
        ]
    }

    import random
    flavour_text = flavour or random.choice(blurbs.get(action, blurbs["generic"]))
    fncPrintMessage(flavour_text, "info")

# ================================================================
# Function: fncEnsureFolder
# Purpose : Create a folder if it does not exist
# Notes   : Returns pathlib.Path object
# ================================================================
def fncEnsureFolder(path: str) -> pathlib.Path:
    p = pathlib.Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ================================================================
# Function: fncLoadEnv
# Purpose : Read environment variable with default
# Notes   : Strips quotes; returns default if empty
# ================================================================
def fncLoadEnv(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name, default)
    if isinstance(val, str):
        return val.strip().strip('"').strip("'")
    return val


# ================================================================
# Function: fncReadJSON
# Purpose : Load JSON from file safely
# Notes   : Returns {} on failure when safe=True
# ================================================================
def fncReadJSON(path: str, safe: bool = True) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as ex:
        if safe:
            fncPrintMessage(f"Could not read JSON '{path}': {ex}", "warn")
            return {}
        raise


# ================================================================
# Function: fncWriteJSON
# Purpose : Write data to JSON with nice formatting
# Notes   : Ensures parent folder exists; UTF-8; 2-space indent
# ================================================================
def fncWriteJSON(path: str, data: Dict[str, Any]) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    fncPrintMessage(f"Saved JSON → {p}", "success")


# ================================================================
# Function: fncExportCSV -- Prob Move to Exports....
# Purpose : Save list[dict] or list[list] to CSV
# Notes   : If rows are dicts, headers are union of keys (sorted)
# ================================================================
def fncExportCSV(path: str, rows: Iterable[Any]) -> None:
    rows = list(rows)
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        with open(p, "w", newline="", encoding="utf-8") as f:
            pass
        fncPrintMessage(f"Created empty CSV → {p}", "warn")
        return

    if isinstance(rows[0], dict):
        headers = sorted({k for r in rows for k in r.keys()})
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in headers})
    else:
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for r in rows:
                w.writerow(list(r))

    fncPrintMessage(f"Saved CSV → {p}", "success")


# ================================================================
# Function: fncTimestamp
# Purpose : Return an ISO8601 timestamp string
# Notes   : Uses local time; suffix 'Z' optional
# ================================================================
def fncTimestamp(zulu: bool = False) -> str:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return ts + ("Z" if zulu else "")


# ================================================================
# Function: fncChunkList
# Purpose : Yield items in fixed-size chunks
# Notes   : Useful for batch Graph calls or rate limiting
# ================================================================
def fncChunkList(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ================================================================
# Function: fncRetry
# Purpose : Simple retry wrapper with backoff
# Notes   : backoff in seconds; returns fn result or raises
# ================================================================
def fncRetry(fn, attempts: int = 3, backoff: float = 1.5, exceptions: Tuple = (Exception,), *args, **kwargs):
    last_ex: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except exceptions as ex:  # pragma: no cover
            last_ex = ex
            if attempt < attempts:
                sleep_for = backoff ** (attempt - 1)
                fncPrintMessage(f"Attempt {attempt}/{attempts} failed: {ex}. Retrying in {sleep_for:.1f}s…", "warn")
                time.sleep(sleep_for)
            else:
                fncPrintMessage(f"All {attempts} attempts failed: {ex}", "error")
                raise
    if last_ex:
        raise last_ex


# ================================================================
# Function: fncSafeGet
# Purpose : Safe nested dictionary access
# Notes   : path like 'a.b.c'; returns default when missing
# ================================================================
def fncSafeGet(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


# ================================================================
# Function: fncToTable
# Purpose : Render rows as a table string
# Notes   : Supports list[dict] (keys become headers) or list[list]
# ================================================================
def fncToTable(rows: Iterable[Any], headers: Optional[List[str]] = None, max_rows: Optional[int] = None) -> str:
    rows = list(rows)
    if max_rows and len(rows) > max_rows:
        rows = rows[:max_rows] + [["…", "…", "…"]]

    if not rows:
        return "(no data)"

    if isinstance(rows[0], dict):
        hdrs = headers or sorted({k for r in rows for k in r.keys()})
        table_rows = [[r.get(h, "") for h in hdrs] for r in rows]
        return tabulate(table_rows, headers=hdrs, tablefmt="github")
    else:
        return tabulate(rows, headers=(headers or "firstrow"), tablefmt="github")


# ================================================================
# Function: fncMask
# Purpose : Mask sensitive strings (client secrets, tokens)
# Notes   : Keeps start/end visible; handles short strings
# ================================================================
def fncMask(value: Optional[str], show: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= show * 2:
        return "*" * len(value)
    return f"{value[:show]}{'*' * (len(value) - (show*2))}{value[-show:]}"


# ================================================================
# Function: fncNewRunId
# Purpose : Generate a short unique run identifier
# Notes   : Useful for correlating logs and outputs
# ================================================================
def fncNewRunId(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ================================================================
# Function: fncPromptYesNo
# Purpose : Simple Y/N prompt for interactive flows
# Notes   : Defaults to 'n' if empty input
# ================================================================
def fncPromptYesNo(question: str, default_no: bool = True) -> bool:
    suffix = "[y/N]" if default_no else "[Y/n]"
    ans = input(f"{question} {suffix} ").strip().lower()
    if not ans:
        return not default_no
    return ans in ("y", "yes")
