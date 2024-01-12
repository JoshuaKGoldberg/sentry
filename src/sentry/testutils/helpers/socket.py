from __future__ import annotations

import contextlib
import ipaddress
from unittest import mock

from sentry.net import socket as net_socket

__all__ = ["override_blacklist"]


@contextlib.contextmanager
def override_blacklist(*ip_addresses: str):
    with mock.patch.object(
        net_socket,
        "DISALLOWED_IPS",
        frozenset(ipaddress.ip_network(ip) for ip in ip_addresses),
    ):
        try:
            yield
        finally:
            # We end up caching these disallowed ips on this function, so
            # make sure we clear the cache as part of cleanup
            net_socket.is_ipaddress_allowed.cache_clear()
