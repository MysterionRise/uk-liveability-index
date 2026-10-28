"""A publisher in maintenance is an outage, not a reshaped file (#219).

uk-air.defra.gov.uk spent the end of October 2026 redirecting every URL to a
"Website Temporarily Unavailable" page that answers 200, so the link resolver searched
the maintenance page for the CSVs, found none and reported the source as broken. A
response that moved to a maintenance path, or a 503, now raises ``InMaintenance``: the
nightly check reports the source as not verified, and a refresh keeps its locked edition
without ``--keep-going``.
"""

from urllib.parse import urlsplit

import requests


class InMaintenance(Exception):
    """The publisher answered with a maintenance page or a 503."""


def raise_if_maintenance(resp: requests.Response, asked: str) -> None:
    host = urlsplit(asked).netloc or asked
    if resp.status_code == 503:
        raise InMaintenance(f"{host} answered 503 (service unavailable)")
    final = urlsplit(resp.url or asked).path
    if final != urlsplit(asked).path and "maintenance" in final.lower():
        raise InMaintenance(f"{host} is in maintenance (redirected to {final})")
