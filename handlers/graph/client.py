# ================================================================
# File     : client.py
# Purpose  : Microsoft Graph API read-only client for Entra (Azure AD)
# Notes    : Read-only: GET + pagination + retries. No destructive ops.
# ================================================================

import os
import msal
import requests
import time
import getpass
from typing import Dict, Any, List, Optional
from core.utils import fncPrintMessage, fncRetry

# ================================================================
# Class    : GraphClient
# Purpose  : Read-only wrapper for Microsoft Graph API
# Notes    : Uses client credentials (app-only). Ensure app has
#           the required Application permissions (read-only) and
#           admin consent granted.
#           Example app perms to grant:
#             - Directory.Read.All (Application)
#             - Application.Read.All (Application)
#             - RoleManagement.Read.Directory (Application)
#             - Policy.Read.All (Application)
# ================================================================
class GraphClient:
    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        # Try environment variables first
        tenant_id = tenant_id or os.getenv("CLOUDPOODLE_TENANT_ID")
        client_id = client_id or os.getenv("CLOUDPOODLE_CLIENT_ID")
        client_secret = client_secret or os.getenv("CLOUDPOODLE_CLIENT_SECRET")

        # Prompt interactively if any credential is missing
        if not tenant_id:
            tenant_id = input("Enter Tenant ID: ").strip()
        if not client_id:
            client_id = input("Enter Application (Client) ID: ").strip()
        if not client_secret:
            fncPrintMessage(
                "No Client Secret found — it will be hidden as you type. "
                "Credentials are stored in environment only for this session.",
                "warn",
            )
            client_secret = getpass.getpass("Enter Client Secret (input hidden): ").strip()

        # Persist to environment for the lifetime of the session
        os.environ["CLOUDPOODLE_TENANT_ID"] = tenant_id
        os.environ["CLOUDPOODLE_CLIENT_ID"] = client_id
        os.environ["CLOUDPOODLE_CLIENT_SECRET"] = client_secret

        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret

        # Application scope (app-only). Ensure the app has appropriate read-only app perms.
        self.scope = ["https://graph.microsoft.com/.default"]
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"

        fncPrintMessage("Initialising Microsoft Graph (read-only) client...", "info")

        # MSAL ConfidentialClientApplication
        self.app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )

        self.token = self._fncGetAccessToken()
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        fncPrintMessage("GraphClient initialised (read-only).", "success")

    # ================================================================
    # Function: _fncGetAccessToken
    # Purpose : Obtain OAuth2 token using MSAL (with silent cache fallback)
    # Notes   : Uses client credentials flow; error raised if token cannot be acquired
    # ================================================================
    def _fncGetAccessToken(self) -> str:
        fncPrintMessage("Requesting Microsoft Graph access token...", "debug")
        result = self.app.acquire_token_silent(self.scope, account=None)
        if not result:
            result = self.app.acquire_token_for_client(scopes=self.scope)

        if "access_token" not in result:
            fncPrintMessage(
                f"MSAL Authentication failed: {result.get('error_description', 'Unknown error')}",
                "error",
            )
            raise Exception("Failed to acquire access token")

        fncPrintMessage("Access token acquired successfully.", "debug")
        return result["access_token"]

    # ================================================================
    # Function: fncHandleResponse
    # Purpose : Handle Graph responses, follow @odata.nextLink and retry 429
    # Notes   : Returns parsed JSON; raises for >=400 (non-429) responses
    # ================================================================
    def fncHandleResponse(self, response: requests.Response) -> Dict[str, Any]:
        status = response.status_code

        # Success
        if status == 200:
            return response.json()

        # Rate limit
        if status == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            fncPrintMessage(f"Rate limit hit. Sleeping for {retry_after}s...", "warn")
            time.sleep(retry_after)
            # Re-send same request
            req = response.request
            resp = requests.request(method=req.method, url=req.url, headers=self.headers, data=req.body)
            return self.fncHandleResponse(resp)

        # Other client/server errors
        if status >= 400:
            fncPrintMessage(f"Graph API Error [{status}] → {response.text}", "error")
            raise Exception(f"Graph API request failed with status {status}")

        # Fallback
        try:
            return response.json()
        except Exception:
            return {"status": status, "text": response.text}

    # ================================================================
    # Function: get
    # Purpose : Perform a GET request to a Graph endpoint (single page)
    # Notes   : Use get_all for paginated resources
    # ================================================================
    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"https://graph.microsoft.com/v1.0/{endpoint.lstrip('/')}"
        fncPrintMessage(f"GET {url}", "debug")
        return fncRetry(lambda: self.fncHandleResponse(requests.get(url, headers=self.headers, params=params)))

    # ================================================================
    # Function: get_all
    # Purpose : Retrieve all items from a paginated Graph endpoint
    # Notes   : Returns a flat list of items (value) for list endpoints
    # ================================================================
    def get_all(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Example: client.get_all("applications?$select=id,displayName")
        """
        url = f"https://graph.microsoft.com/v1.0/{endpoint.lstrip('/')}"
        fncPrintMessage(f"GET (all pages) {url}", "debug")

        def _single_page(req_url):
            resp = requests.get(req_url, headers=self.headers, params=params)
            return self.fncHandleResponse(resp)

        data = fncRetry(lambda: _single_page(url))
        items: List[Dict[str, Any]] = []

        # If the response is a single resource (not a list)
        if isinstance(data, dict) and "value" not in data:
            # Not a collection — return as single-element list for convenience
            return [data]

        if isinstance(data, dict) and "value" in data:
            items.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink")
        else:
            # Unexpected shape — return empty
            return items

        while next_link:
            fncPrintMessage(f"Following nextLink → {next_link}", "debug")
            resp = requests.get(next_link, headers=self.headers)
            page = self.fncHandleResponse(resp)
            if isinstance(page, dict):
                items.extend(page.get("value", []))
                next_link = page.get("@odata.nextLink")
            else:
                break

        return items
