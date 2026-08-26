"""URL safety validation — SSRF protection.

A user-supplied (or LLM-supplied) URL must never be allowed to point at
internal infrastructure. The MVP blocks the well-known dangerous ranges
and any non-HTTP(S) scheme.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Allowed URL schemes. Everything else (file://, ftp://, gopher://, ...) is rejected.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames that must always be blocked even if DNS would otherwise resolve them publicly.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
)

# CIDR ranges that are unsafe to call from server-side fetchers.
_BLOCKED_CIDRS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),       # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),         # loopback
    ipaddress.ip_network("169.254.0.0/16"),      # link-local / GCP metadata
    ipaddress.ip_network("172.16.0.0/12"),       # private
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),      # private
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),         # multicast
    ipaddress.ip_network("240.0.0.0/4"),         # reserved
    ipaddress.ip_network("::1/128"),             # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),            # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
]


class SSRFError(ValueError):
    """Raised when a URL points at unsafe / internal infrastructure."""


def _resolve_host(host: str) -> list[str]:
    """Resolve hostname to IP literal strings. Empty list if resolution fails."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return list({info[4][0] for info in infos})


def is_private_ip(ip_str: str) -> bool:
    """True if the IP literal belongs to a blocked CIDR range."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # refuse to make a decision on garbage
    return any(ip in net for net in _BLOCKED_CIDRS)


def assert_safe_url(url: str) -> str:
    """Validate a URL for server-side fetching.

    Returns the normalised URL on success. Raises SSRFError otherwise.
    """
    if not url:
        raise SSRFError("Empty URL")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFError(f"Disallowed scheme: {parsed.scheme!r}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFError("Missing hostname")

    if host in _BLOCKED_HOSTNAMES:
        raise SSRFError(f"Blocked hostname: {host}")

    # Reject literal IPs that point into private ranges.
    try:
        ip = ipaddress.ip_address(host)
        if is_private_ip(str(ip)):
            raise SSRFError(f"Private IP literal: {host}")
    except ValueError:
        # Not a literal — resolve via DNS.
        for resolved in _resolve_host(host):
            if is_private_ip(resolved):
                raise SSRFError(f"DNS resolves to private IP: {host} -> {resolved}")

    return url