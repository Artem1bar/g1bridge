"""Agent registry: defaults are valid, TOML overrides merge, bad specs fail fast."""

from pathlib import Path

import pytest

from g1bridge.agents import (
    DEFAULT_AGENTS,
    MAX_BLURB_CHARS,
    MAX_NAME_CHARS,
    AgentSpec,
    load_agents,
    validate_spec,
)


def test_defaults_are_valid_and_unique():
    ids = [spec.id for spec in DEFAULT_AGENTS]
    assert len(ids) == len(set(ids))
    assert "ask" in ids
    for spec in DEFAULT_AGENTS:
        validate_spec(spec)  # must not raise


def test_tools_follow_web_flag():
    web = AgentSpec("a", "A", "blurb", "prompt", web=True)
    offline = AgentSpec("b", "B", "blurb", "prompt", web=False)
    assert web.tools == ("WebSearch", "WebFetch")
    assert offline.tools == ()


@pytest.mark.parametrize(
    "field, value",
    [
        ("id", "Has Space"),
        ("id", ""),
        ("name", "x" * (MAX_NAME_CHARS + 1)),
        ("name", ""),
        ("blurb", "y" * (MAX_BLURB_CHARS + 1)),
        ("system_prompt", "   "),
    ],
)
def test_validate_rejects_bad_fields(field, value):
    kwargs = dict(id="ok", name="Ok", blurb="fine", system_prompt="do things")
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        validate_spec(AgentSpec(**kwargs))


def test_missing_file_gives_defaults(tmp_path: Path):
    assert load_agents(tmp_path / "nope.toml") == DEFAULT_AGENTS


def test_toml_overrides_replace_in_place_and_append(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text(
        """
[[agent]]
id = "ask"
name = "Ask!"
blurb = "custom ask"
system_prompt = "be terse"
web = false

[[agent]]
id = "recipes"
name = "Recipes"
blurb = "what to cook"
system_prompt = "suggest a dish"
"""
    )
    agents = load_agents(path)
    ids = [spec.id for spec in agents]
    assert ids[: len(DEFAULT_AGENTS)] == [spec.id for spec in DEFAULT_AGENTS]
    assert ids[-1] == "recipes"
    ask = agents[ids.index("ask")]
    assert ask.name == "Ask!" and ask.web is False
    recipes = agents[-1]
    assert recipes.web is True and recipes.model is None


def test_toml_bad_spec_names_the_field(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text('[[agent]]\nid = "x"\nname = ""\nblurb = ""\nsystem_prompt = "p"\n')
    with pytest.raises(ValueError, match="name"):
        load_agents(path)


def test_toml_unknown_key_fails(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text(
        '[[agent]]\nid = "x"\nname = "X"\nblurb = ""\nsystem_prompt = "p"\ncolour = 1\n'
    )
    with pytest.raises(ValueError, match="colour"):
        load_agents(path)


def test_toml_duplicate_ids_fail(tmp_path: Path):
    path = tmp_path / "agents.toml"
    entry = '[[agent]]\nid = "custom"\nname = "C"\nblurb = ""\nsystem_prompt = "p"\n'
    path.write_text(entry + entry)
    with pytest.raises(ValueError, match="duplicate.*custom"):
        load_agents(path)


def test_toml_syntax_error_is_a_value_error(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text("[[agent]\nid = ")
    with pytest.raises(ValueError, match="TOML"):
        load_agents(path)


def test_unreadable_file_is_a_value_error(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text("")
    path.chmod(0o000)
    try:
        with pytest.raises(ValueError, match="cannot read"):
            load_agents(path)
    finally:
        path.chmod(0o600)
