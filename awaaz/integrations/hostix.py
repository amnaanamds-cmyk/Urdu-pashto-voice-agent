"""HOSTIX room availability for hostels.

ASSUMED CONTRACT — adjust to the real HOSTIX API before the hostel pilot:

  GET {base_url}/api/rooms?type=<room type>
  Authorization: Bearer <api key>
  200 -> [{"type": "2 seater", "available": 3, "fee_monthly": 12000,
           "meals_included": true}, ...]

The agent only ever repeats what this returns (never invents fees), so if the
call fails the tool returns an error and the agent offers to take a message.
"""

from __future__ import annotations

import httpx

from .secrets import resolve


class HostixError(Exception):
    pass


def get_rooms(integration: dict, room_type: str | None = None,
              client: httpx.Client | None = None) -> list[dict]:
    if not integration or not integration.get("base_url"):
        raise HostixError("HOSTIX is not connected for this business")
    client = client or httpx.Client(timeout=4)   # keep it short: the caller is waiting
    try:
        r = client.get(
            integration["base_url"].rstrip("/") + "/api/rooms",
            params={"type": room_type} if room_type else None,
            headers={"Authorization": f"Bearer {resolve(integration['api_key_ref'])}"},
        )
        r.raise_for_status()
        rooms = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise HostixError(f"HOSTIX unavailable: {e}") from e
    if not isinstance(rooms, list):
        raise HostixError("unexpected HOSTIX response")
    return [
        {
            "type": str(x.get("type", "")),
            "available": int(x.get("available", 0)),
            "fee_monthly": x.get("fee_monthly"),
            "meals_included": x.get("meals_included"),
        }
        for x in rooms if isinstance(x, dict)
    ]
