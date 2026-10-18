"""
RerouteAI — Travel Disruption Recovery Concierge Agent
Built with Google ADK 2.11 Workflow Graph API and Gemini 2.5 Flash
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import sys
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.events import RequestInput
from google.adk.tools import agent_tool
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.adk.workflow import Edge, FunctionNode, START, Workflow
from mcp.client.stdio import StdioServerParameters

from .config import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MCP Toolset — connects to mcp_server.py via stdio
# ─────────────────────────────────────────────────────────────────────────────

MCP_TOOLSET = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
        )
    )
)

# ─────────────────────────────────────────────────────────────────────────────
# SPECIALIST SUB-AGENTS
# ─────────────────────────────────────────────────────────────────────────────

disruption_detector = LlmAgent(
    name="DisruptionDetectorAgent",
    model=config.model,
    tools=[MCP_TOOLSET],
    instruction="""You are the Disruption Detector for RerouteAI.
Your job:
1. Parse the disruption event details (flight, train, bus, hotel, cab).
2. Identify the root booking that is directly affected (e.g. BK001).
3. Use the tool `get_booking_details` with booking_id="BK001" (or the affected ID) to fetch full booking details.
4. Output a JSON object with keys:
   - root_booking_id: str (e.g. "BK001")
   - disruption_type: "CANCELLED" | "DELAYED" | "OVERBOOKED"
   - delay_minutes: int (0 if cancelled/overbooked)
   - affected_service: str (e.g. "IndiGo 6E-501 BLR → IXE")
   - new_departure_time: str
   - traveller_summary: str
Rules:
- Output valid JSON only.
- Never invent booking IDs not present in the system.
""",
)

impact_analyzer = LlmAgent(
    name="ImpactAnalyzerAgent",
    model=config.model,
    tools=[MCP_TOOLSET],
    instruction="""You are the Impact Analyzer for RerouteAI.
Your job:
1. Use tool `get_itinerary` to retrieve all bookings in dependency order (e.g. BK001 -> BK002 -> BK003 -> BK004).
2. Starting from the root disrupted booking (e.g. BK001 delayed by 240 mins), cascade forward:
   - Check which subsequent bookings DEPEND ON the disrupted one (e.g. cab BK002 timed for arrival, hotel BK003 check-in).
3. For each affected booking, determine:
   - why it is affected (dependency chain, missed connection, slack lost)
   - the slack time lost in minutes
   - whether it is directly or transitively affected
4. Return a structured JSON object:
   {
     "affected_chain": [
       {
         "booking_id": str,
         "service": str,
         "impact_reason": str,
         "slack_lost_minutes": int,
         "is_root": bool
       }
     ],
     "total_affected": int
   }
Rules:
- Output valid JSON only.
- Preserve exact booking IDs.
""",
)

recovery_planner = LlmAgent(
    name="RecoveryPlannerAgent",
    model=config.model,
    tools=[MCP_TOOLSET],
    instruction="""You are the Recovery Planner for RerouteAI.
Your job:
1. Use tool `search_alternatives` with booking_id="BK001" and disruption_type="DELAYED" to find available alternatives.
2. Filter alternatives according to traveller hard constraints:
   - max_extra_budget_inr: maximum extra spend in INR allowed (e.g. 5000 INR)
   - latest_arrival_time: must reach before the deadline
   - seats_available: must be > 0
3. Assemble EXACTLY 4 distinct recovery plans from the valid options:
   - FASTEST: minimises total travel time
   - CHEAPEST: minimises additional out-of-pocket spend
   - MOST_COMFORTABLE: best comfort score, respects traveller preferences
   - AI_RECOMMENDED: your balanced choice with clear, plain-language reasoning
4. For each plan provide:
   {
     "plan_id": "FASTEST" | "CHEAPEST" | "MOST_COMFORTABLE" | "AI_RECOMMENDED",
     "summary": str,
     "total_extra_cost_inr": int,
     "new_arrival_time": str,
     "reasoning": str,
     "options": [ { "service": str, "route": str, "departure": str, "arrival": str, "extra_cost_inr": int } ]
   }
5. Return JSON:
   { "recovery_plans": [ ...4 plans... ] }
Rules:
- ONLY use transport options returned by search_alternatives.
- Output valid JSON only.
""",
)

# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR AGENT
# ─────────────────────────────────────────────────────────────────────────────

orchestrator = LlmAgent(
    name="RerouteOrchestrator",
    model=config.model,
    tools=[
        agent_tool.AgentTool(agent=disruption_detector),
        agent_tool.AgentTool(agent=impact_analyzer),
        agent_tool.AgentTool(agent=recovery_planner),
        MCP_TOOLSET,
    ],
    instruction="""You are the RerouteAI Orchestrator — an elite AI travel disruption concierge for Karnataka and Indian travel networks.
You coordinate three specialist agents in sequence:
1. DisruptionDetectorAgent — validates the disruption on the root booking (e.g. BK001).
2. ImpactAnalyzerAgent — retrieves the full itinerary via get_itinerary and cascades the impact downstream (BK002 cab, BK003 hotel, etc.).
3. RecoveryPlannerAgent — searches alternatives via search_alternatives and builds 4 ranked recovery plans (FASTEST, CHEAPEST, MOST_COMFORTABLE, AI_RECOMMENDED).

When invoked:
1. Delegate to DisruptionDetectorAgent to analyze the disruption.
2. Delegate to ImpactAnalyzerAgent to compute the downstream impact chain.
3. Delegate to RecoveryPlannerAgent to produce the 4 recovery plans matching traveller constraints.
4. Synthesize everything into an empathetic, ultra-clear executive summary with:
   - 🚨 **Disruption Assessment**: Root cause and arrival delay.
   - ⛓️ **Downstream Impact Chain**: Which dependent bookings are broken and why.
   - 📋 **4 Ranked Recovery Plans**:
       1. ⚡ **FASTEST** (timings, extra cost INR, trade-offs)
       2. 💰 **CHEAPEST** (timings, extra cost INR, trade-offs)
       3. 🛋️ **MOST_COMFORTABLE** (timings, extra cost INR, trade-offs)
       4. 🌟 **AI_RECOMMENDED** (balanced choice with reasoning)
   - ⚠️ **Human-in-the-Loop Notice**: Clear notice asking the traveller to review and approve their preferred plan ID before any rebooking is confirmed.

Be professional, concise, and structured with clean markdown.
""",
)

# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW NODES
# ─────────────────────────────────────────────────────────────────────────────

def load_input(ctx: Context) -> str:
    """Parse and validate incoming request payload into ctx.state."""
    user_text = ""
    if ctx.user_content and ctx.user_content.parts:
        user_text = "".join(p.text for p in ctx.user_content.parts if p.text).strip()
    elif ctx.state.get("__input__"):
        val = ctx.state["__input__"]
        user_text = val if isinstance(val, str) else json.dumps(val)

    payload: dict[str, Any] = {}
    if user_text:
        json_match = re.search(r"\{.*\}", user_text, re.DOTALL)
        if json_match:
            try:
                payload = json.loads(json_match.group(0))
            except Exception:
                payload = {}

    # If missing or unstructured, populate canonical disruption scenario (BLR -> IXE delay)
    if not payload or not isinstance(payload, dict) or "disruption_event" not in payload:
        booking_id = "BK001"
        if "BK00" in user_text:
            m = re.search(r"BK00\d", user_text)
            if m:
                booking_id = m.group(0)

        payload = {
            "disruption_event": {
                "booking_id": booking_id,
                "disruption_type": "DELAYED",
                "delay_minutes": 240,
                "affected_service": "IndiGo 6E-501 BLR → IXE",
                "user_prompt": user_text or "IndiGo 6E-501 BLR to IXE delayed 4 hours",
            },
            "traveller_profile": {
                "name": "Aditi Sharma",
                "max_extra_budget_inr": 5000,
                "latest_arrival_time": "2026-10-20T23:59:00+05:30",
                "must_reach_destination": "Udupi",
                "preferences": {
                    "avoid_night_travel": True,
                    "prefer_comfort": True,
                },
            },
            "all_bookings": [
                {"id": "BK001", "service": "IndiGo 6E-501", "type": "FLIGHT", "route": "BLR → IXE"},
                {"id": "BK002", "service": "Ola Outstation", "type": "CAB", "route": "IXE → Udupi", "depends_on": ["BK001"]},
                {"id": "BK003", "service": "Ideal Beach Resort", "type": "HOTEL", "route": "Udupi", "depends_on": ["BK002"]},
                {"id": "BK004", "service": "Matsyagandha Express", "type": "TRAIN", "route": "Udupi → SBC", "depends_on": ["BK003"]},
            ],
            "raw_input": user_text or "Default BLR->IXE disruption test scenario",
        }

    ctx.state["disruption_event"] = payload.get("disruption_event", {})
    ctx.state["traveller_profile"] = payload.get("traveller_profile", {})
    ctx.state["all_bookings"] = payload.get("all_bookings", [])
    ctx.state["raw_input"] = payload

    ctx.route = "OK"
    return "OK"


def security_checkpoint(ctx: Context) -> str:
    """PII scrubbing, prompt injection detection, and budget sanity audit."""
    audit = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "node": "security_checkpoint",
        "checks": [],
        "severity": "INFO",
        "passed": True,
    }

    # 1. PII Scrubbing
    pii_patterns = {
        "aadhaar": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
        "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
        "phone_in": r"\b(?:\+91|0)?[6-9]\d{9}\b",
        "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "passport": r"\b[A-Z][0-9]{7}\b",
        "credit_card": r"\b(?:\d{4}[- ]?){3}\d{4}\b",
    }

    scrubbed_fields: list[str] = []
    for field in ["disruption_event", "traveller_profile", "all_bookings"]:
        field_val = ctx.state.get(field, {})
        val_str = json.dumps(field_val)
        for pii_type, pattern in pii_patterns.items():
            if re.search(pattern, val_str):
                scrubbed_fields.append(f"{field}.{pii_type}")
                val_str = re.sub(pattern, f"[REDACTED_{pii_type.upper()}]", val_str)
        if scrubbed_fields:
            try:
                ctx.state[field] = json.loads(val_str)
            except Exception:
                pass

    audit["checks"].append({
        "type": "PII_SCRUBBING",
        "fields_scrubbed": scrubbed_fields,
        "count": len(scrubbed_fields),
    })
    if scrubbed_fields:
        audit["severity"] = "WARNING"

    # 2. Prompt Injection Detection
    injection_keywords = [
        "ignore previous instructions",
        "disregard all",
        "you are now",
        "act as",
        "jailbreak",
        "override system",
        "forget your instructions",
        "new persona",
        "sudo mode",
        "developer mode",
        "bypass security",
        "reveal your prompt",
        "show your system prompt",
    ]

    raw_str = json.dumps(ctx.state.get("raw_input", {})).lower()
    detected_keywords = [kw for kw in injection_keywords if kw in raw_str]

    if detected_keywords:
        audit["checks"].append({
            "type": "INJECTION_DETECTION",
            "detected_keywords": detected_keywords,
        })
        audit["severity"] = "CRITICAL"
        audit["passed"] = False
        ctx.state["security_audit"] = audit
        logger.warning("SECURITY_EVENT: Prompt injection detected: %s", detected_keywords)
        ctx.route = "SECURITY_EVENT"
        return "SECURITY_EVENT"

    audit["checks"].append({"type": "INJECTION_DETECTION", "result": "CLEAN"})

    # 3. Domain Rule: Budget Sanity Check
    profile = ctx.state.get("traveller_profile", {})
    budget = profile.get("max_extra_budget_inr", 0)
    if not isinstance(budget, (int, float)) or budget < 0:
        audit["checks"].append({
            "type": "BUDGET_SANITY",
            "result": "INVALID",
            "value": budget,
        })
        audit["severity"] = "WARNING"
    else:
        audit["checks"].append({
            "type": "BUDGET_SANITY",
            "result": "OK",
            "value": budget,
        })

    ctx.state["security_audit"] = audit
    ctx.route = "CLEAN"

    # Pass structured prompt directly to orchestrator
    event = ctx.state.get("disruption_event", {})
    return (
        f"Travel Disruption Detected!\n"
        f"- Affected Service: {event.get('affected_service', 'IndiGo 6E-501 BLR → IXE')}\n"
        f"- Disruption: {event.get('disruption_type', 'DELAYED')} by {event.get('delay_minutes', 240)} minutes\n"
        f"- Root Booking ID: {event.get('booking_id', 'BK001')}\n"
        f"- Traveller Profile: Budget INR {profile.get('max_extra_budget_inr', 5000)}, Destination: {profile.get('must_reach_destination', 'Udupi')}\n"
        f"Please analyze the cascade impact across all bookings and recommend 4 recovery plans."
    )


def security_blocked(ctx: Context) -> str:
    """Safely terminate when a security event is detected."""
    ctx.state["final_output"] = {
        "status": "BLOCKED",
        "reason": "Request blocked by security checkpoint. Potential prompt injection or policy violation.",
        "audit": ctx.state.get("security_audit", {}),
    }
    return "BLOCKED"


def input_error(ctx: Context) -> str:
    """Surface input validation failures."""
    ctx.state["final_output"] = {
        "status": "INPUT_ERROR",
        "reason": ctx.state.get("error", "Invalid or missing input parameters"),
    }
    return "INPUT_ERROR"


def human_approval(ctx: Context) -> RequestInput | str:
    """Human-in-the-loop pause: traveller reviews recovery plans before rebooking."""
    interrupt_key = "traveller_approval"
    if interrupt_key in ctx.resume_inputs:
        decision = ctx.resume_inputs[interrupt_key]
        ctx.state["traveller_approval"] = decision
        return f"Traveller decision received: {decision}"

    summary = ctx.output or ctx.state.get("orchestrator_output", "Recovery plans ready for review.")
    return RequestInput(
        interrupt_id=interrupt_key,
        message=(
            f"### 🛎️ RerouteAI — Recovery Plans Ready for Review\n\n"
            f"{summary}\n\n"
            "─────────────────────────────────────────────────────────────\n"
            "**Action Required**: Reply with your preferred plan ID:\n"
            "- `FASTEST`\n"
            "- `CHEAPEST`\n"
            "- `MOST_COMFORTABLE`\n"
            "- `AI_RECOMMENDED`\n"
            "Or type `REJECT` to cancel without modifying bookings.\n"
        ),
    )


def process_approval(ctx: Context) -> str:
    """Process traveller approval decision and execute simulated rebooking."""
    raw_decision = str(ctx.state.get("traveller_approval", "AI_RECOMMENDED")).strip().upper()
    valid_plans = ["FASTEST", "CHEAPEST", "MOST_COMFORTABLE", "AI_RECOMMENDED"]

    if "REJECT" in raw_decision:
        ctx.state["final_output"] = {
            "status": "REJECTED",
            "message": "Traveller opted not to proceed with rebooking. All existing reservations remain unchanged.",
            "audit": ctx.state.get("security_audit", {}),
        }
        return "REJECTED"

    chosen_plan = "AI_RECOMMENDED"
    for p in valid_plans:
        if p in raw_decision:
            chosen_plan = p
            break

    ctx.state["final_output"] = {
        "status": "APPROVED",
        "approved_plan": chosen_plan,
        "message": (
            f"Plan '{chosen_plan}' approved by traveller! "
            f"Simulated rebooking confirmed across all impacted connections (IndiGo flight / cab / hotel)."
        ),
        "audit": ctx.state.get("security_audit", {}),
    }
    return "APPROVED"


def finalize(ctx: Context) -> str:
    """Compile and present the final output with audit trail."""
    output = ctx.state.get("final_output", {})
    status = output.get("status", "COMPLETED")
    msg = output.get("message", "Recovery workflow concluded.")
    audit = ctx.state.get("security_audit", {})

    return (
        f"## ✈️ RerouteAI — Recovery Concierge Summary\n\n"
        f"**Status:** `{status}`\n\n"
        f"{msg}\n\n"
        f"### 🛡️ Security Checkpoint Summary\n"
        f"- **Audited At:** {audit.get('timestamp', 'N/A')}\n"
        f"- **Severity:** `{audit.get('severity', 'INFO')}`\n"
        f"- **Passed:** {'✅ Yes' if audit.get('passed', True) else '❌ No'}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW GRAPH DEFINITION
# ─────────────────────────────────────────────────────────────────────────────

node_load = FunctionNode(func=load_input, name="load_input")
node_sec = FunctionNode(func=security_checkpoint, name="security_checkpoint")
node_sec_blocked = FunctionNode(func=security_blocked, name="security_blocked")
node_input_err = FunctionNode(func=input_error, name="input_error")
node_hitl = FunctionNode(func=human_approval, name="human_approval", rerun_on_resume=True)
node_proc = FunctionNode(func=process_approval, name="process_approval")
node_fin = FunctionNode(func=finalize, name="finalize")

edges = [
    # Entry point from START
    Edge(from_node=START, to_node=node_load),
    # Input router
    Edge(from_node=node_load, to_node=node_sec, route="OK"),
    Edge(from_node=node_load, to_node=node_input_err, route="INPUT_ERROR"),
    # Security gate
    Edge(from_node=node_sec, to_node=orchestrator, route="CLEAN"),
    Edge(from_node=node_sec, to_node=node_sec_blocked, route="SECURITY_EVENT"),
    # Orchestrator to Human-in-the-loop review
    Edge(from_node=orchestrator, to_node=node_hitl),
    # Approval to rebooking execution
    Edge(from_node=node_hitl, to_node=node_proc),
    # Convergence on terminal node
    Edge(from_node=node_proc, to_node=node_fin),
    Edge(from_node=node_input_err, to_node=node_fin),
    Edge(from_node=node_sec_blocked, to_node=node_fin),
]

root_agent = Workflow(
    name="RerouteAI",
    description=(
        "Travel disruption recovery concierge: detects disruptions, cascades impact "
        "across all dependent bookings, produces 4 ranked recovery plans, and awaits "
        "human approval before confirming any rebooking."
    ),
    edges=edges,
)

app = root_agent
