"""Agent registry: what the hub menu lists and how each Claude agent is prompted.

Built-in agents live in DEFAULT_AGENTS. The wearer can replace or extend them
with `~/.g1bridge-agents.toml`:

    [[agent]]
    id = "recipes"              # a-z, 0-9, '-' or '_'
    name = "Recipes"            # menu label, at most 12 chars
    blurb = "what to cook"      # menu hint, at most 26 chars
    system_prompt = "Suggest one dish from the ingredients the wearer names."
    web = false                 # WebSearch/WebFetch allowed? (default true)
    # model = "claude-haiku-4-5" # optional per-agent model override

An entry whose id matches a built-in agent replaces it in place; new ids are
appended to the menu.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

AGENTS_PATH = Path.home() / ".g1bridge-agents.toml"
MAX_NAME_CHARS = 12
MAX_BLURB_CHARS = 26
WEB_TOOLS = ("WebSearch", "WebFetch")
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,15}$")
_REQUIRED_KEYS = frozenset({"id", "name", "blurb", "system_prompt"})


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    blurb: str
    system_prompt: str
    web: bool = True
    model: str | None = None

    @property
    def tools(self) -> tuple[str, ...]:
        return WEB_TOOLS if self.web else ()


def validate_spec(spec: AgentSpec) -> None:
    """Raise ValueError naming the offending field; the hub menu has hard limits."""
    if not isinstance(spec.id, str) or not _ID_RE.match(spec.id):
        raise ValueError(
            f"agent id {spec.id!r}: use 1-16 chars of a-z, 0-9, '-' or '_', "
            "starting with a letter"
        )
    if not isinstance(spec.name, str) or not 1 <= len(spec.name) <= MAX_NAME_CHARS:
        raise ValueError(f"agent {spec.id!r}: name must be 1-{MAX_NAME_CHARS} chars")
    if not isinstance(spec.blurb, str) or len(spec.blurb) > MAX_BLURB_CHARS:
        raise ValueError(
            f"agent {spec.id!r}: blurb must be at most {MAX_BLURB_CHARS} chars"
        )
    if not isinstance(spec.system_prompt, str) or not spec.system_prompt.strip():
        raise ValueError(f"agent {spec.id!r}: system_prompt must not be empty")
    if not isinstance(spec.web, bool):
        raise ValueError(f"agent {spec.id!r}: web must be true or false")
    if spec.model is not None and not isinstance(spec.model, str):
        raise ValueError(f"agent {spec.id!r}: model must be a string")


DEFAULT_AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        "ask",
        "Ask",
        "quick answers, web on",
        "Answer the wearer's question directly. Use web search only when the "
        "answer depends on current facts (news, prices, hours, weather).",
    ),
    AgentSpec(
        "research",
        "Research",
        "digs in, cites sources",
        "Research the question with web search and web fetch before answering. "
        "Lead with the finding, then name one or two sources by site name. "
        "You may use up to 120 words.",
    ),
    AgentSpec(
        "translate",
        "Translate",
        "any language <-> English",
        "Translate what the wearer says. Non-English input becomes English. "
        "English input becomes the target language the wearer named earlier in "
        "this conversation; if none was named, ask once which language to use. "
        "Reply with the translation only.",
        web=False,
    ),
    AgentSpec(
        "explain",
        "Explain",
        "a term or idea, simply",
        "Explain the term or idea the wearer names in plain language a smart "
        "teenager would follow. One concrete example if it helps. Search the web "
        "only for terms you do not recognise.",
    ),
    AgentSpec(
        "draft",
        "Draft",
        "short messages & replies",
        "Write the short message the wearer describes: a text, an email, a reply. "
        "Match the tone they ask for. Output only the message text.",
        web=False,
    ),
)


def load_agents(path: Path = AGENTS_PATH) -> tuple[AgentSpec, ...]:
    """Built-in agents merged with the wearer's TOML overrides, validated."""
    if not path.exists():
        return DEFAULT_AGENTS
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ValueError(f"{path}: cannot read agents file ({exc.strerror})") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: not valid TOML ({exc})") from exc
    entries = data.get("agent", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected [[agent]] tables")
    overrides = tuple(_spec_from_table(path, entry) for entry in entries)
    duplicates = sorted(
        {spec.id for spec in overrides if [s.id for s in overrides].count(spec.id) > 1}
    )
    if duplicates:
        raise ValueError(f"{path}: duplicate agent id(s) {duplicates}")
    return merge_agents(DEFAULT_AGENTS, overrides)


def merge_agents(
    base: tuple[AgentSpec, ...], overrides: tuple[AgentSpec, ...]
) -> tuple[AgentSpec, ...]:
    """Overrides replace same-id base entries in place; new ids go at the end."""
    replaced = tuple(
        next((spec for spec in overrides if spec.id == original.id), original)
        for original in base
    )
    base_ids = {spec.id for spec in base}
    appended = tuple(spec for spec in overrides if spec.id not in base_ids)
    return replaced + appended


def _spec_from_table(path: Path, table: object) -> AgentSpec:
    label = table.get("id", "?") if isinstance(table, dict) else "?"
    if not isinstance(table, dict):
        raise ValueError(f"{path}: each [[agent]] must be a table")
    allowed = {field.name for field in fields(AgentSpec)}
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"{path}: agent {label!r} has unknown key(s) {unknown}")
    missing = sorted(_REQUIRED_KEYS - set(table))
    if missing:
        raise ValueError(f"{path}: agent {label!r} is missing {missing}")
    spec = AgentSpec(**table)
    try:
        validate_spec(spec)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return spec
