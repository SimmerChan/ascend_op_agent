# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")

"""Tests for U5 /learn command (sync run_conversation injection — A2 fix)."""

from __future__ import annotations

import pytest

from ascend_op_agent.cli import (
    _AUTHORING_STANDARDS,
    _handle_learn_command,
    build_learn_prompt,
)


def test_build_learn_prompt_empty_returns_empty_string():
    """Empty / whitespace user_request → "" (caller handles AE5a tutorial)."""
    assert build_learn_prompt("") == ""
    assert build_learn_prompt("   ") == ""
    assert build_learn_prompt(None) == ""  # type: ignore[arg-type]


def test_build_learn_prompt_nonempty_inlines_authoring_standards():
    """Non-empty → prompt contains user request + _AUTHORING_STANDARDS body."""
    prompt = build_learn_prompt("ops_pt build.sh 配置")
    assert "ops_pt build.sh 配置" in prompt
    assert "## Project Scope" in prompt  # authoring standards
    assert 'skill_manage(action="create")' in prompt  # action hint
    assert "When to Use" in prompt
    assert "Pitfalls" in prompt
    assert "Verification" in prompt


def test_build_learn_prompt_mentions_task_types():
    """Prompt hints task_type ∈ TASK_TYPES so agent fills metadata correctly."""
    prompt = build_learn_prompt("foo")
    assert "migrate" in prompt
    assert "develop" in prompt
    assert "topic" in prompt


def test_authoring_standards_has_nine_sections():
    """_AUTHORING_STANDARDS lists the 9 mandatory body sections."""
    sections = [
        "Project Scope",
        "When to Use",
        "Prerequisites",
        "How to Run",
        "Quick Reference",
        "Procedure",
        "Pitfalls",
        "Verification",
        "Related Skills",
    ]
    for s in sections:
        assert s in _AUTHORING_STANDARDS, f"missing section: {s}"


def test_authoring_standards_enforces_description_40_chars():
    """C5 alignment: standards say description ≤40 chars (matches U2/KTD-2)."""
    assert "≤40" in _AUTHORING_STANDARDS or "40 chars" in _AUTHORING_STANDARDS


def test_authoring_standards_forbids_cannbot_overlap():
    """Standards warn agent not to duplicate cannbot-covered通用知识."""
    assert "cannbot" in _AUTHORING_STANDARDS.lower()


def test_handle_learn_command_empty_returns_tutorial():
    """AE5a: empty user_request → tutorial, no agent call."""
    out = _handle_learn_command("")
    assert out["status"] == "tutorial"
    assert "Usage" in out["response"]
    assert "/learn" in out["response"]


def test_handle_learn_command_whitespace_returns_tutorial():
    out = _handle_learn_command("   \t  ")
    assert out["status"] == "tutorial"


def test_handle_learn_command_nonempty_calls_run_callable_sync():
    """A2 fix: non-empty → sync call run_callable with built prompt."""
    calls: list[str] = []

    def recorder(prompt: str) -> str:
        calls.append(prompt)
        return "skill written: tiling-pitfalls"

    out = _handle_learn_command("the build.sh fix", run_callable=recorder)
    assert out["status"] == "crystallized"
    assert out["response"] == "skill written: tiling-pitfalls"
    assert len(calls) == 1
    assert "the build.sh fix" in calls[0]
    assert "## Project Scope" in calls[0]  # prompt has authoring standards


def test_handle_learn_command_no_run_callable_returns_error():
    """Non-empty input but no run_callable → error (caller must wire agent)."""
    out = _handle_learn_command("foo", run_callable=None)
    assert out["status"] == "error"
    assert "run_callable" in out["response"]


def test_handle_learn_command_run_callable_exception_returns_error():
    """If run_callable raises, _handle_learn_command catches and returns error."""

    def boom(prompt: str) -> str:
        raise RuntimeError("LLM timeout")

    out = _handle_learn_command("foo", run_callable=boom)
    assert out["status"] == "error"
    assert "LLM timeout" in out["response"]


def test_handle_learn_command_uses_pending_input_nowhere():
    """A2 verification: code must not reference _pending_input as an identifier.

    Checks AST Name/Attribute nodes only — docstrings mentioning the A2 fix
    (``no `_pending_input` queue``) are intentional explanatory text, not code.
    """
    import ast
    import inspect

    def code_identifiers(func) -> set[str]:
        tree = ast.parse(inspect.getsource(func))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        return names

    assert "_pending_input" not in code_identifiers(_handle_learn_command)
    assert "_pending_input" not in code_identifiers(build_learn_prompt)


def test_learn_click_command_registered():
    """The `learn` Click command is registered on the main group."""
    from ascend_op_agent.cli import main

    cmd_names = list(main.commands.keys())
    assert "learn" in cmd_names
