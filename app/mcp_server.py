"""
RerouteAI — MCP Server (stdio transport)
Exposes 5 tools for the travel disruption recovery workflow.
Mock inventory covers Karnataka-relevant routes (BLR, IXE, Udupi, Puttur, Mangaluru).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

app = Server("reroute-ai-mcp")

# ─────────────────────────────────────────────────────────────────────────────
# MOCK DATA — bookings & alternatives
# ─────────────────────────────────────────────────────────────────────────────

MOCK_BOOKINGS: dict[str, dict[str, Any]] = {
    "BK001": {
        "id": "BK001",
        "type": "FLIGHT",
        "service": "IndiGo 6E-501",
        "route": "BLR → IXE",
        "departure": "2026-10-20T06:30:00+05:30",
        "arrival": "2026-10-20T08:10:00+05:30",
        "status": "CONFIRMED",
        "price_inr": 4200,
        "depends_on": [],
        "dependents": ["BK002", "BK003"],
    },
    "BK002": {
        "id": "BK002",
        "type": "CAB",
        "service": "Ola Outstation — Mangaluru Airport → Udupi",
        "route": "IXE → Udupi",
        "departure": "2026-10-20T08:40:00+05:30",
        "arrival": "2026-10-20T10:00:00+05:30",
        "status": "CONFIRMED",
        "price_inr": 950,
        "depends_on": ["BK001"],
        "dependents": ["BK003"],
    },
    "BK003": {
        "id": "BK003",
        "type": "HOTEL",
        "service": "Ideal Beach Resort, Udupi",
        "route": "Udupi",
        "departure": "2026-10-20T12:00:00+05:30",
        "arrival": None,
        "status": "CONFIRMED",
        "price_inr": 3500,
        "check_in": "2026-10-20T12:00:00+05:30",
        "depends_on": ["BK002"],
        "dependents": ["BK004"],
    },
    "BK004": {
        "id": "BK004",
        "type": "TRAIN",
        "service": "Matsyagandha Express 12620",
        "route": "Udupi → SBC (Bengaluru)",
        "departure": "2026-10-21T18:00:00+05:30",
        "arrival": "2026-10-22T05:00:00+05:30",
        "status": "CONFIRMED",
        "price_inr": 620,
        "depends_on": ["BK003"],
        "dependents": [],
    },
}

FULL_ITINERARY = list(MOCK_BOOKINGS.values())

# Karnataka-relevant transport alternatives
ALTERNATIVES: dict[str, list[dict[str, Any]]] = {
    "FLIGHT_BLR_IXE": [
        {
            "option_id": "ALT_F1",
            "type": "FLIGHT",
            "service": "Air India AI-485",
            "route": "BLR → IXE",
            "departure": "2026-10-20T10:15:00+05:30",
            "arrival": "2026-10-20T11:55:00+05:30",
            "price_inr": 5100,
            "extra_cost_inr": 900,
            "seats_available": 6,
            "comfort_score": 8,
        },
        {
            "option_id": "ALT_F2",
            "type": "FLIGHT",
            "service": "IndiGo 6E-543",
            "route": "BLR → IXE",
            "departure": "2026-10-20T13:45:00+05:30",
            "arrival": "2026-10-20T15:25:00+05:30",
            "price_inr": 3800,
            "extra_cost_inr": 0,
            "seats_available": 12,
            "comfort_score": 7,
        },
    ],
    "TRAIN_BLR_MAQ": [
        {
            "option_id": "ALT_T1",
            "type": "TRAIN",
            "service": "Konkan Kanya Express 10111",
            "route": "KSR Bengaluru → Mangaluru Central",
            "departure": "2026-10-20T07:30:00+05:30",
            "arrival": "2026-10-20T15:45:00+05:30",
            "price_inr": 580,
            "extra_cost_inr": 0,
            "seats_available": 34,
            "comfort_score": 7,
        },
        {
            "option_id": "ALT_T2",
            "type": "TRAIN",
            "service": "Mangaluru Express 12133",
            "route": "KSR Bengaluru → Mangaluru Junction",
            "departure": "2026-10-20T20:00:00+05:30",
            "arrival": "2026-10-21T07:15:00+05:30",
            "price_inr": 490,
            "extra_cost_inr": 0,
            "seats_available": 8,
            "comfort_score": 6,
        },
    ],
    "BUS_BLR_MAQ": [
        {
            "option_id": "ALT_B1",
            "type": "BUS",
            "service": "KSRTC Airavata Club Class",
            "route": "Bengaluru → Mangaluru",
            "departure": "2026-10-20T21:00:00+05:30",
            "arrival": "2026-10-21T06:30:00+05:30",
            "price_inr": 900,
            "extra_cost_inr": 0,
            "seats_available": 5,
            "comfort_score": 7,
        },
        {
            "option_id": "ALT_B2",
            "type": "BUS",
            "service": "VRL Travels Volvo AC",
            "route": "Bengaluru → Mangaluru",
            "departure": "2026-10-20T22:30:00+05:30",
            "arrival": "2026-10-21T07:00:00+05:30",
            "price_inr": 950,
            "extra_cost_inr": 0,
            "seats_available": 10,
            "comfort_score": 8,
        },
    ],
    "CAB_IXE_UDUPI": [
        {
            "option_id": "ALT_C1",
            "type": "CAB",
            "service": "Rapido Outstation",
            "route": "IXE → Udupi",
            "departure": "FLEXIBLE",
            "arrival": "FLEXIBLE",
            "price_inr": 850,
            "extra_cost_inr": 0,
            "seats_available": 99,
            "comfort_score": 7,
        },
    ],
    "HOTEL_UDUPI": [
        {
            "option_id": "ALT_H1",
            "type": "HOTEL",
            "service": "Manipal Residency, Udupi",
            "route": "Udupi",
            "price_inr": 2800,
            "extra_cost_inr": 0,
            "rooms_available": 3,
            "comfort_score": 7,
            "check_in": "FLEXIBLE",
        },
        {
            "option_id": "ALT_H2",
            "type": "HOTEL",
            "service": "Hotel Shaan International, Udupi",
            "route": "Udupi",
            "price_inr": 1900,
            "extra_cost_inr": 0,
            "rooms_available": 5,
            "comfort_score": 6,
            "check_in": "FLEXIBLE",
        },
    ],
}

RECOVERY_LOG: list[dict[str, Any]] = []

# ─────────────────────────────────────────────────────────────────────────────
# TOOL DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="get_booking_details",
        description=(
            "Retrieve full details of a specific booking by its ID. "
            "Returns booking type, route, times, price, status, and dependencies."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "string",
                    "description": "The booking ID to look up (e.g. BK001)",
                }
            },
            "required": ["booking_id"],
        },
    ),
    Tool(
        name="get_itinerary",
        description=(
            "Retrieve the complete traveller itinerary as an ordered list of bookings, "
            "including dependency links between them."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="search_alternatives",
        description=(
            "Search available alternative transport or accommodation options for a disrupted booking. "
            "Filters by route type and returns options with price, availability, and comfort score."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "string",
                    "description": "ID of the disrupted booking to find alternatives for",
                },
                "disruption_type": {
                    "type": "string",
                    "enum": ["CANCELLED", "DELAYED", "OVERBOOKED"],
                    "description": "The type of disruption that occurred",
                },
                "delay_minutes": {
                    "type": "integer",
                    "description": "How many minutes delayed (0 for CANCELLED/OVERBOOKED)",
                    "default": 0,
                },
            },
            "required": ["booking_id", "disruption_type"],
        },
    ),
    Tool(
        name="confirm_rebooking",
        description=(
            "Record the traveller's confirmed rebooking decision after human approval. "
            "Logs the old and new bookings for the audit trail. Returns a confirmation receipt."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "approved_plan_id": {
                    "type": "string",
                    "description": "The plan ID approved by the traveller (e.g. FASTEST)",
                },
                "bookings_to_replace": {
                    "type": "array",
                    "description": "List of {old_booking_id, new_option_id} pairs to rebook",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_booking_id": {"type": "string"},
                            "new_option_id": {"type": "string"},
                        },
                        "required": ["old_booking_id", "new_option_id"],
                    },
                },
            },
            "required": ["approved_plan_id", "bookings_to_replace"],
        },
    ),
    Tool(
        name="get_audit_log",
        description=(
            "Retrieve the full audit trail of all agent decisions, tool calls, and rebooking actions "
            "recorded during this session."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# TOOL HANDLERS
# ─────────────────────────────────────────────────────────────────────────────


def _route_key_for_booking(booking_id: str) -> str:
    """Map a booking ID to an alternatives dictionary key."""
    mapping = {
        "BK001": "FLIGHT_BLR_IXE",
        "BK002": "CAB_IXE_UDUPI",
        "BK003": "HOTEL_UDUPI",
        "BK004": "TRAIN_BLR_MAQ",  # return leg alternatives (for illustrative use)
    }
    return mapping.get(booking_id, "")


def handle_get_booking_details(args: dict) -> str:
    booking_id = args.get("booking_id", "")
    booking = MOCK_BOOKINGS.get(booking_id)
    if not booking:
        return json.dumps({"error": f"Booking '{booking_id}' not found."})
    _log_action("get_booking_details", args, {"found": True, "type": booking["type"]})
    return json.dumps(booking, indent=2)


def handle_get_itinerary(_args: dict) -> str:
    _log_action("get_itinerary", {}, {"count": len(FULL_ITINERARY)})
    return json.dumps({"itinerary": FULL_ITINERARY}, indent=2)


def handle_search_alternatives(args: dict) -> str:
    booking_id = args.get("booking_id", "")
    disruption_type = args.get("disruption_type", "")
    delay_minutes = args.get("delay_minutes", 0)

    route_key = _route_key_for_booking(booking_id)
    options = ALTERNATIVES.get(route_key, [])

    # Also offer train/bus alternatives for flight disruptions
    extra: list[dict] = []
    if booking_id == "BK001" and disruption_type in ("CANCELLED", "OVERBOOKED"):
        extra = ALTERNATIVES.get("TRAIN_BLR_MAQ", []) + ALTERNATIVES.get("BUS_BLR_MAQ", [])

    all_options = options + extra
    _log_action(
        "search_alternatives",
        args,
        {"route_key": route_key, "options_found": len(all_options)},
    )
    return json.dumps(
        {
            "booking_id": booking_id,
            "disruption_type": disruption_type,
            "delay_minutes": delay_minutes,
            "alternatives": all_options,
            "total": len(all_options),
        },
        indent=2,
    )


def handle_confirm_rebooking(args: dict) -> str:
    plan_id = args.get("approved_plan_id", "")
    replacements = args.get("bookings_to_replace", [])

    receipts = []
    for r in replacements:
        old_id = r.get("old_booking_id", "")
        new_id = r.get("new_option_id", "")
        receipts.append(
            {
                "old_booking_id": old_id,
                "new_option_id": new_id,
                "status": "CONFIRMED_SIMULATED",
                "confirmation_code": f"RR-{old_id}-{new_id[:6]}",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        )

    result = {
        "plan_id": plan_id,
        "receipts": receipts,
        "message": (
            f"Plan '{plan_id}' confirmed (simulated). "
            "In production, this would trigger actual rebooking APIs."
        ),
    }
    _log_action("confirm_rebooking", args, {"receipts": len(receipts)})
    return json.dumps(result, indent=2)


def handle_get_audit_log(_args: dict) -> str:
    return json.dumps({"audit_log": RECOVERY_LOG, "total_entries": len(RECOVERY_LOG)}, indent=2)


def _log_action(tool: str, inputs: dict, outputs: dict) -> None:
    RECOVERY_LOG.append(
        {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "tool": tool,
            "inputs": inputs,
            "outputs": outputs,
            "severity": "INFO",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# MCP SERVER HANDLERS
# ─────────────────────────────────────────────────────────────────────────────


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "get_booking_details": handle_get_booking_details,
        "get_itinerary": handle_get_itinerary,
        "search_alternatives": handle_search_alternatives,
        "confirm_rebooking": handle_confirm_rebooking,
        "get_audit_log": handle_get_audit_log,
    }
    handler = handlers.get(name)
    if not handler:
        result = json.dumps({"error": f"Unknown tool: {name}"})
    else:
        try:
            result = handler(arguments or {})
        except Exception as exc:
            logger.exception("Tool '%s' raised an error", name)
            result = json.dumps({"error": str(exc)})

    return [TextContent(type="text", text=result)]


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
