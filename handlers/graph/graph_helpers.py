# ================================================================
# File     : core/graph_helpers.py
# Purpose  : Safer Graph helpers (handle missing $select fields)
# Notes    : Warn instead of fail; add "Not Found" placeholders.
# ================================================================

import re
from typing import List, Dict, Any, Tuple
from core.utils import fncPrintMessage

def safe_select_get_all(client, base_endpoint: str, fields: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Calls client.get_all with a $select list. If Graph returns 400 with
    "Could not find a property named 'X'", we warn, drop X, retry once,
    and add X = "Not Found" to every returned row.
    Returns: (items, missing_fields)
    """
    endpoint = f"{base_endpoint}?$select={','.join(fields)}" if fields else base_endpoint
    try:
        items = client.get_all(endpoint)
        # ensure explicit keys exist
        for it in items:
            for f in fields:
                it.setdefault(f, it.get(f, None))
        return items, []
    except Exception as ex:
        msg = str(ex)
        m = re.search(r"Could not find a property named '([^']+)'", msg)
        if not m:
            raise  # different error; bubble up

        missing = m.group(1)
        if missing in fields:
            fncPrintMessage(f"Property not found: '{missing}' — retrying without it.", "warn")
            retry_fields = [f for f in fields if f != missing]
            items, more_missing = safe_select_get_all(client, base_endpoint, retry_fields)
            # add placeholder for the missing field
            for it in items:
                it[missing] = "Not Found"
            return items, [missing] + more_missing
        raise
