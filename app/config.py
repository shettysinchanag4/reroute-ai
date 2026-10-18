import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "False")  # Gemini API key only


@dataclass
class AgentConfig:
    # Reads model from environment GEMINI_MODEL.
    # Default: gemini-3.5-flash-lite (active free-tier quota; gemini-3.5-flash and gemini-3.8-flash have 20 req/day cap).
    model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    mcp_server_port: int = 8090
    max_iterations: int = 3
    pii_redaction_enabled: bool = True
    injection_detection_enabled: bool = True


config = AgentConfig()

# ─────────────────────────────────────────────────────────────────────────────
# Resilient Rate Limiting & User-Friendly 429 Quota Error Handling
# ─────────────────────────────────────────────────────────────────────────────
import asyncio
import logging
import re

_logger = logging.getLogger(__name__)

try:
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    _orig_generate = Gemini.generate_content_async

    def _parse_quota_error(err_str: str) -> dict:
        """Parse Gemini 429 / RESOURCE_EXHAUSTED error details."""
        is_daily = False
        retry_delay_seconds = None
        retry_time_human = None

        delay_match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", err_str)
        if delay_match:
            retry_delay_seconds = float(delay_match.group(1))

        human_match = re.search(r"retry in\s+([\d\w\.]+[smhd])", err_str)
        if human_match:
            retry_time_human = human_match.group(1)
            if not retry_delay_seconds:
                h = re.search(r"(\d+)h", retry_time_human)
                m = re.search(r"(\d+)m", retry_time_human)
                s = re.search(r"([\d\.]+)s", retry_time_human)
                total = 0.0
                if h:
                    total += float(h.group(1)) * 3600
                if m:
                    total += float(m.group(1)) * 60
                if s:
                    total += float(s.group(1))
                if total > 0:
                    retry_delay_seconds = total

        # Daily quota criteria: explicit PerDay quota ID, limit: 20/day, or retry delay > 60 seconds (e.g. 14 hours)
        if any(k in err_str for k in ["GenerateRequestsPerDay", "limit: 20", "per day", "PerDay"]):
            is_daily = True
        elif retry_delay_seconds and retry_delay_seconds > 60:
            is_daily = True

        return {
            "is_daily": is_daily,
            "retry_delay_seconds": retry_delay_seconds,
            "retry_time_human": retry_time_human or (f"{int(retry_delay_seconds)}s" if retry_delay_seconds else "a few seconds"),
        }

    async def _resilient_generate_content_async(self, *args, **kwargs):
        model_name = getattr(self, "model", config.model)
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                async for response in _orig_generate(self, *args, **kwargs):
                    yield response
                return
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    quota_info = _parse_quota_error(err_msg)

                    # Case 1: Daily free-tier request quota exhausted (Stop immediately, do not retry)
                    if quota_info["is_daily"]:
                        _logger.warning(
                            "[Gemini Quota] Daily quota limit reached for model %s (reset period: %s). Quota exhausted; stopping retries.",
                            model_name,
                            quota_info["retry_time_human"],
                        )
                        friendly_msg = (
                            f"### ⚠️ Gemini API Quota Exceeded (429 RESOURCE_EXHAUSTED)\n\n"
                            f"The free-tier daily request quota for model **`{model_name}`** has been exceeded.\n\n"
                            f"- **Server Notice:** Please retry in `{quota_info['retry_time_human']}`\n\n"
                            f"**How to Resolve & Continue:**\n"
                            f"1. **Switch to an available model:** Set `GEMINI_MODEL=gemini-3.5-flash-lite` in your `.env` file.\n"
                            f"2. **Restart ADK Web Server:** `$env:UV_LINK_MODE=\"copy\"; uv run adk web app --host 127.0.0.1 --port 18081`\n"
                            f"3. **Upgrade Tier:** Attach a billing account in [Google AI Studio](https://aistudio.google.com/) for higher quotas."
                        )
                        yield LlmResponse(
                            content=types.Content(
                                role="model",
                                parts=[types.Part.from_text(text=friendly_msg)],
                            ),
                            turn_complete=True,
                        )
                        return

                    # Case 2: Per-minute rate limit (transient 429) -> Exponential backoff with server retry-after
                    if attempt < max_attempts - 1:
                        server_delay = quota_info.get("retry_delay_seconds")
                        base_backoff = min(2.0 ** attempt * 4.0, 30.0)
                        wait_seconds = max(server_delay or base_backoff, 2.0)
                        _logger.warning(
                            "[Gemini Rate Limit] 429 rate limit reached for %s. Backing off for %.1fs (attempt %d/%d)...",
                            model_name,
                            wait_seconds,
                            attempt + 1,
                            max_attempts,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue

                    # Rate limit retries exhausted
                    _logger.warning(
                        "[Gemini Rate Limit] Rate limit retries exhausted for %s after %d attempts.",
                        model_name,
                        max_attempts,
                    )
                    friendly_msg = (
                        f"### ⚠️ Gemini API Rate Limit (429)\n\n"
                        f"The short-term rate limit for model **`{model_name}`** was reached after {max_attempts} retries.\n\n"
                        f"- **Server Notice:** Please retry in `{quota_info['retry_time_human']}`\n"
                        f"- Please wait a moment before sending another prompt."
                    )
                    yield LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=friendly_msg)],
                        ),
                        turn_complete=True,
                    )
                    return

                # Non-quota error, re-raise
                raise

    Gemini.generate_content_async = _resilient_generate_content_async
except Exception as patch_err:
    _logger.debug("Could not patch Gemini: %s", patch_err)

# ─────────────────────────────────────────────────────────────────────────────
# ADK ApiServer Dev UI Compatibility for Workflow Root Agents
# ─────────────────────────────────────────────────────────────────────────────
try:
    from google.adk.cli.api_server import ApiServer

    _orig_get_root = ApiServer._get_root_agent

    def _custom_get_root(self, agent_or_app):
        root = _orig_get_root(self, agent_or_app)
        if hasattr(root, "_orchestrator"):
            return root._orchestrator
        return root

    ApiServer._get_root_agent = _custom_get_root
except Exception as patch_err:
    _logger.debug("Could not patch ApiServer: %s", patch_err)

