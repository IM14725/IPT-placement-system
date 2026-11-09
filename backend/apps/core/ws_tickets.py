"""Single-use WebSocket tickets.

The browser authenticates to the realtime service with a short-lived ticket instead of
a permanent API token. The realtime service redeems it with an atomic GETDEL
(``realtime/app/core/auth.py``), so a ticket works once. The key format is shared.
"""

import secrets

from apps.core.redis_client import get_redis

WS_TICKET_TTL_SECONDS = 60


def ws_ticket_key(ticket):
    return f"ipt:wsticket:{ticket}"


def issue_ws_ticket(user):
    ticket = secrets.token_urlsafe(32)
    get_redis().set(ws_ticket_key(ticket), str(user.id), ex=WS_TICKET_TTL_SECONDS)
    return ticket
