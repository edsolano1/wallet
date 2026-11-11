"""Thin wrapper around the Plaid SDK. Nothing else in the app imports plaid directly."""

import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest

from . import config

_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def client() -> plaid_api.PlaidApi:
    host = _HOSTS.get(config.PLAID_ENV, plaid.Environment.Sandbox)
    cfg = plaid.Configuration(
        host=host,
        api_key={"clientId": config.PLAID_CLIENT_ID, "secret": config.PLAID_SECRET},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(cfg))


def create_link_token() -> str:
    """A short lived token that lets the Plaid popup open. Safe to send to the page."""
    req = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id="local-user"),
        client_name="Personal Finance",
        products=[Products("transactions")],
        country_codes=[CountryCode("US")],
        language="en",
    )
    return client().link_token_create(req)["link_token"]


def exchange_public_token(public_token: str) -> tuple[str, str]:
    """Swap the popup's throwaway token for the long lived access token."""
    res = client().item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    return res["access_token"], res["item_id"]


def get_accounts(access_token: str) -> list[dict]:
    res = client().accounts_get(AccountsGetRequest(access_token=access_token))
    return res.to_dict()["accounts"]


def get_institution_name(access_token: str) -> str | None:
    """Best effort. A missing institution name must never break linking."""
    try:
        item = client().item_get(ItemGetRequest(access_token=access_token)).to_dict()["item"]
        inst_id = item.get("institution_id")
        if not inst_id:
            return None
        res = client().institutions_get_by_id(
            InstitutionsGetByIdRequest(
                institution_id=inst_id, country_codes=[CountryCode("US")]
            )
        )
        return res.to_dict()["institution"]["name"]
    except Exception:
        return None


def sync_transactions(access_token: str, cursor: str | None):
    """
    One page of /transactions/sync. Returns (added, modified, removed, next_cursor, has_more).

    This is the newer sync pattern, not /transactions/get. Plaid hands back only what
    changed since the cursor, so each call is small and nothing is counted twice.
    """
    from plaid.model.transactions_sync_request import TransactionsSyncRequest

    kwargs = {"access_token": access_token}
    if cursor:
        kwargs["cursor"] = cursor
    res = client().transactions_sync(TransactionsSyncRequest(**kwargs)).to_dict()
    return (
        res.get("added", []),
        res.get("modified", []),
        res.get("removed", []),
        res.get("next_cursor"),
        res.get("has_more", False),
    )


def get_item_status(access_token: str) -> dict:
    """
    Ask Plaid how a connection is doing. A broken Item reports an error here
    even when the last sync looked fine, so this is checked on every sync.
    """
    from plaid.model.item_get_request import ItemGetRequest

    item = client().item_get(ItemGetRequest(access_token=access_token)).to_dict()["item"]
    err = item.get("error") or {}
    return {
        "healthy": not err,
        "error_code": err.get("error_code"),
        "error_message": err.get("error_message"),
    }


def create_update_link_token(access_token: str) -> str:
    """
    A link token in update mode. Opens Plaid Link straight into the
    reconnect flow for one bank, instead of asking which bank to add.
    """
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.country_code import CountryCode

    req = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id="local-user"),
        client_name="Personal Finance",
        country_codes=[CountryCode("US")],
        language="en",
        access_token=access_token,
    )
    return client().link_token_create(req)["link_token"]
