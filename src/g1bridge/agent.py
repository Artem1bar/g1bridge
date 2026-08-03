"""Claude agent layer — a HUD-tuned assistant over the Claude Agent SDK.

The SDK drives the local `claude` CLI, so requests bill the user's existing
Claude subscription (same mechanism as Claude Code). No API key needed.
"""

from __future__ import annotations

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

HUD_SYSTEM_PROMPT = """\
You are a voice-style assistant whose answers appear on the tiny monochrome
heads-up display of Even Realities G1 smart glasses. The display fits 5 short
lines per page and the wearer must tap their temple to turn pages.

Rules:
- Plain text only. No markdown, no asterisks, no headers, no tables, no emoji.
- Lead with the answer; add detail only if it earns its place.
- Default to under 50 words. Never exceed 120 words unless explicitly asked.
- Short sentences. No preamble like "Here is..." and no closing questions.
"""


class GlassesAgent:
    """A multi-turn Claude session tuned for the G1 display."""

    def __init__(self, *, model: str | None = None, web_search: bool = True):
        tools = ["WebSearch", "WebFetch"] if web_search else []
        self._client = ClaudeSDKClient(
            ClaudeAgentOptions(
                system_prompt=HUD_SYSTEM_PROMPT,
                tools=tools,
                allowed_tools=list(tools),
                # Deny anything not allow-listed instead of blocking on a prompt.
                permission_mode="dontAsk",
                model=model,
            )
        )

    async def __aenter__(self) -> "GlassesAgent":
        await self._client.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        await self._client.disconnect()
        return False

    async def ask(self, prompt: str) -> str:
        """Send one user turn; returns the assistant's final text."""
        await self._client.query(prompt)
        chunks: list[str] = []
        result: ResultMessage | None = None
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                chunks.extend(
                    block.text
                    for block in message.content
                    if isinstance(block, TextBlock)
                )
            elif isinstance(message, ResultMessage):
                result = message
        text = "\n".join(chunks).strip()
        if not text and result is not None and result.result:
            text = result.result.strip()
        if not text and result is not None and result.is_error:
            text = "Agent error. Check the terminal for details."
        return text or "(no answer)"
