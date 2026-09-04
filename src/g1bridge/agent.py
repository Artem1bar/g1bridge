"""Claude agent layer — a HUD-tuned assistant over the Claude Agent SDK.

The SDK drives the local `claude` CLI, so requests bill the user's existing
Claude subscription (same mechanism as Claude Code). No API key needed.
"""

from __future__ import annotations

from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

from .agents import AgentSpec

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


def hub_system_prompt(spec: AgentSpec) -> str:
    return (
        f"{HUD_SYSTEM_PROMPT}\n"
        f'You are the "{spec.name}" agent in the wearer\'s hub. Your role:\n'
        f"{spec.system_prompt.strip()}\n"
    )


class GlassesAgent:
    """A multi-turn Claude session tuned for the G1 display."""

    def __init__(
        self,
        *,
        system_prompt: str = HUD_SYSTEM_PROMPT,
        model: str | None = None,
        web_search: bool = True,
    ):
        tools = ["WebSearch", "WebFetch"] if web_search else []
        self._client = ClaudeSDKClient(
            ClaudeAgentOptions(
                system_prompt=system_prompt,
                tools=tools,
                allowed_tools=list(tools),
                # Deny anything not allow-listed instead of blocking on a prompt.
                permission_mode="dontAsk",
                model=model,
                include_partial_messages=True,  # stream text deltas to the HUD
            )
        )

    @classmethod
    def for_spec(
        cls, spec: AgentSpec, *, model: str | None = None, web_search: bool = True
    ) -> "GlassesAgent":
        """A hub agent: the HUD rules plus the spec's own role prompt."""
        return cls(
            system_prompt=hub_system_prompt(spec),
            model=spec.model or model,
            web_search=web_search and spec.web,
        )

    async def __aenter__(self) -> "GlassesAgent":
        await self._client.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        await self._client.disconnect()
        return False

    async def ask(self, prompt: str) -> str:
        """Send one user turn; returns the assistant's final text."""
        final = ""
        async for final in self.ask_stream(prompt):
            pass
        return final

    async def ask_stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield the growing answer as it streams; the last item is the final text."""
        await self._client.query(prompt)
        streamed: list[str] = []
        chunks: list[str] = []
        result: ResultMessage | None = None
        async for message in self._client.receive_response():
            if isinstance(message, StreamEvent):
                if message.event.get("type") == "message_start":
                    streamed = []  # a new turn (after a tool call): start over
                delta = text_delta(message.event)
                if delta:
                    streamed.append(delta)
                    yield "".join(streamed)
            elif isinstance(message, AssistantMessage):
                # Keep only the latest turn: text before a web search is narration.
                latest = [
                    block.text
                    for block in message.content
                    if isinstance(block, TextBlock)
                ]
                if latest:
                    chunks = latest
            elif isinstance(message, ResultMessage):
                result = message
        text = "\n".join(chunks).strip() or "".join(streamed).strip()
        if not text and result is not None and result.result:
            text = result.result.strip()
        if not text and result is not None and result.is_error:
            text = "Agent error. Check the terminal for details."
        yield text or "(no answer)"


def text_delta(event: dict) -> str:
    """The text carried by a raw Anthropic stream event, or '' for anything else."""
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    if delta.get("type") != "text_delta":
        return ""
    return str(delta.get("text") or "")
