"""The host's own DNS servers, for guests whose resolver we have to configure.

A microsandbox guest does not inherit the host's resolver configuration, so the
nameservers are passed in when the VM is created. Hardcoding public resolvers
works on a network that answers them and fails completely on one whose DNS is
internal, where every name lookup inside a guest times out.

This module is duplicated in `dev_mcp_server`: each MCP server is installed into its own
virtual environment in the packaged layout, so neither can import the other.
Keep the two copies and their tests in step.
"""

import ipaddress
import logging
import re
import subprocess
import sys
from pathlib import Path

# Used only when the host's configuration cannot be read or names nothing
# usable. Always announced, because a silent fallback is what let the guests'
# DNS stay broken on an internal-DNS network without anyone noticing.
FALLBACK_NAMESERVERS = ("1.1.1.1", "8.8.8.8")

_SCUTIL_NAMESERVER = re.compile(r"^\s*nameserver\[\d+\]\s*:\s*(\S+)", re.MULTILINE)
_RESOLV_NAMESERVER = re.compile(r"^\s*nameserver\s+(\S+)", re.MULTILINE)


def _usable(candidates: list[str]) -> tuple[str, ...]:
    """IPv4 addresses only, in order, without duplicates.

    A guest's network is IPv4-only, so an IPv6 resolver is worse than useless
    there: it is tried first and has to time out before the list moves on.
    """
    seen: dict[str, None] = {}
    for candidate in candidates:
        try:
            seen[str(ipaddress.IPv4Address(candidate))] = None
        except ValueError:
            continue
    return tuple(seen)


def parse_scutil_dns(text: str) -> tuple[str, ...]:
    """Nameservers from `scutil --dns` output.

    Only the unscoped section is read; everything from the scoped-queries
    heading onwards repeats the same servers once per interface.
    """
    unscoped = text.split("DNS configuration (for scoped queries)")[0]
    return _usable(_SCUTIL_NAMESERVER.findall(unscoped))


def parse_resolv_conf(text: str) -> tuple[str, ...]:
    """Nameservers from the contents of an /etc/resolv.conf."""
    body = "\n".join(
        line for line in text.splitlines() if line.lstrip()[:1] not in ("#", ";")
    )
    return _usable(_RESOLV_NAMESERVER.findall(body))


def _read_scutil() -> str:
    return subprocess.run(
        ["scutil", "--dns"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout


def _read_resolv_conf() -> str:
    return Path("/etc/resolv.conf").read_text()


def host_nameservers() -> tuple[str, ...]:
    """The resolvers this host uses, to hand to a guest.

    On darwin `/etc/resolv.conf` is a generated courtesy copy that nothing
    consults and that is absent on some configurations, so the system
    configuration is asked directly.
    """
    try:
        if sys.platform == "darwin":
            nameservers = parse_scutil_dns(_read_scutil())
        else:
            nameservers = parse_resolv_conf(_read_resolv_conf())
    except Exception:
        logging.warning("Could not read the host's DNS configuration", exc_info=True)
        nameservers = ()

    if not nameservers:
        logging.warning(
            "No host nameservers found; guests will use %s, which a network with "
            "internal DNS will not answer",
            ", ".join(FALLBACK_NAMESERVERS),
        )
        return FALLBACK_NAMESERVERS
    return nameservers
