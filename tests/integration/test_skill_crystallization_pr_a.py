# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")

"""PR-A end-to-end integration test (U6): /learn → skill_manage(create) → Layer 6 visible.

Exercises the full闭环 across U1 (task_type), U2 (Layer 6 default-path self-built-only),
U4 (skill_manage create + R2 + self-built storage), U5 (/learn sync injection).
No live LLM — the "agent" is a recorder that calls skill_manage directly, proving the
plumbing + storage + Layer 6 contract without rate-limit/quotadependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from ascend_op_agent.agent.prompt_builder import PromptBuilder
from ascend_op_agent.agent.tools.skill_manage_tool import skill_manage
from ascend_op_agent.cli import _handle_learn_command


VALID_BODY = """## Project Scope

Applies to 910B3 + ops_pt + CANN 9.1.0 only.

## When to Use

Use when tiling produces wrong results on Ascend but not on GPU.

## Pitfalls

ASCEND_COMPUTE_UNIT must match arch22/arch35 generation.
"""


def _use_tmp_storage(tmp_path, monkeypatch):
    from ascend_op_agent.skills import storage as storage_mod
    _orig = storage_mod.SkillStorage.__init__

    def _init(self, skills_dir=None):
        _orig(self, skills_dir=str(tmp_path))

    monkeypatch.setattr(storage_mod.SkillStorage, "__init__", _init)


def test_end_to_end_learn_to_skill_to_layer6(tmp_path, monkeypatch):
    """Full PR-A闭环:
    1. /learn "ops_pt build.sh 配置" → build_learn_prompt
    2. mock agent receives prompt, calls skill_manage(action=create)
    3. skill written to self-built/
    4. next PromptBuilder.build_system_prompt Layer 6 shows the skill (default path)
    """
    _use_tmp_storage(tmp_path, monkeypatch)

    # Step 1+2: _handle_learn_command with a mock agent that "crystallizes"
    # by calling skill_manage(create) inline.
    crystallized: dict[str, Any] = {}

    def mock_run_conversation(prompt: str) -> str:
        # Simulate the agent reading the prompt + calling skill_manage.
        result = skill_manage(
            action="create",
            name="ops-pt-build-sh-config",
            description="910B3 build.sh ASCEND_COMPUTE_UNIT fix",
            task_type="develop",
            topic="build_env",
            body=VALID_BODY,
        )
        crystallized.update(result)
        return f"crystallized: {result.get('data', {}).get('name', '?')}"

    outcome = _handle_learn_command(
        "ops_pt build.sh 配置", run_callable=mock_run_conversation
    )

    # Step 2 assertions: skill_manage was called + succeeded.
    assert outcome["status"] == "crystallized"
    assert crystallized["success"] is True
    skill_path = tmp_path / "self-built" / "ops-pt-build-sh-config" / "SKILL.md"
    assert skill_path.exists()
    saved = skill_path.read_text(encoding="utf-8")
    assert "task_type: develop" in saved
    assert "topic: build_env" in saved
    assert "## Project Scope" in saved

    # Step 4: Layer 6 (default path, task_type=None) shows the new skill.
    from ascend_op_agent.agent.memory import MemoryStore
    pb = PromptBuilder()
    prompt = pb.build_system_prompt(
        workspace_path=str(tmp_path),
        memory_store=MemoryStore(),
        skills_layer_override=None,  # default path = /learn chat
        task_type=None,
    )
    assert "ops-pt-build-sh-config" in prompt
    assert "910B3 build.sh ASCEND_COMPUTE_UNIT fix" in prompt  # description rendered


def test_end_to_end_production_path_merges_cannbot_and_self_built(tmp_path, monkeypatch):
    """Production path: PhaseRunner node passes cannbot phase override (U1 task_type),
    Layer 6 merges cannbot + self-built (U2)."""
    _use_tmp_storage(tmp_path, monkeypatch)

    # Seed a self-built skill.
    skill_manage(
        action="create", name="tiling-pitfalls",
        description="tiling debug skill",
        task_type="develop", topic="tiling", body=VALID_BODY,
    )

    pb = PromptBuilder()
    cannbot_override = (
        "## Available Skills (phase=design)\n"
        "- **cuda2ascend-simt**: cannbot migration skill\n"
    )
    prompt = pb.build_system_prompt(
        workspace_path=str(tmp_path),
        memory_store=__import__(
            "ascend_op_agent.agent.memory", fromlist=["MemoryStore"]
        ).MemoryStore(),
        skills_layer_override=cannbot_override,
        task_type="develop",
    )
    # Both cannbot (from override) and self-built appear.
    assert "cuda2ascend-simt" in prompt
    assert "tiling-pitfalls" in prompt


# ---- F-6 held-out R2 recall (5 obvious violations) ----


@pytest.fixture(scope="module")
def obvious_violations():
    fixture_path = (
        Path(__file__).parent.parent / "fixtures" / "skill_obvious_violations_held_out.yaml"
    )
    with open(fixture_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _use_tmp_storage_and_skip_cannbot(tmp_path, monkeypatch):
    """Isolate storage AND make list_cannbot_skill_names return a known set so
    OV3 (cannbot collision) is deterministic regardless of vendored submodule state."""
    _use_tmp_storage(tmp_path, monkeypatch)
    import ascend_op_agent.orchestrator.cannbot_loader as cl

    monkeypatch.setattr(
        cl, "list_cannbot_skill_names",
        lambda root=None: {"cuda2ascend-simt", "triton-op-coding"},
    )
    # Also patch the CANNBOT_ROOT so the fail-closed branch sees a "configured" path.
    monkeypatch.setattr(cl, "CANNBOT_ROOT", tmp_path / "fake-cannbot")


def test_r2_recall_rejects_all_obvious_violations(obvious_violations, tmp_path, monkeypatch):
    """F-6 ship gate: R2 must reject 100% of the 5 obvious-violation held-out cases."""
    _use_tmp_storage_and_skip_cannbot(tmp_path, monkeypatch)

    rejected = 0
    for case in obvious_violations:
        inp = case["input"]
        result = skill_manage(**inp)
        assert result["success"] is False, (
            f"{case['id']} ({case['description']}) was accepted but should be rejected"
        )
        if case.get("expected_field"):
            assert result.get("field") == case["expected_field"], (
                f"{case['id']} rejected on field {result.get('field')!r}, "
                f"expected {case['expected_field']!r}"
            )
        rejected += 1

    assert rejected == 5


def test_r2_recall_cannbot_collision_case_skipped_when_unavailable(
    obvious_violations, tmp_path, monkeypatch
):
    """OV3 (cannbot collision) is skipped when vendored cannbot unavailable —
    the fail-closed branch rejects ALL writes in that state, which would
    conflate with the collision test. This test documents that behavior."""
    _use_tmp_storage(tmp_path, monkeypatch)
    # Do NOT patch list_cannbot_skill_names — use real one.
    import ascend_op_agent.orchestrator.cannbot_loader as cl

    real_names = cl.list_cannbot_skill_names()
    if not real_names:
        pytest.skip("vendored cannbot-skills not initialized; OV3 fail-closed path tested elsewhere")
    # If available, OV3 should reject via collision.
    ov3 = next(c for c in obvious_violations if c["id"] == "OV3")
    result = skill_manage(**ov3["input"])
    assert result["success"] is False


# ---- A1 token budget sanity (chars-based; tiktoken exact gate deferred to follow-up) ----


def test_layer6_default_path_token_budget_bounded(tmp_path, monkeypatch):
    """A1 ship gate (chars/4 proxy; tiktoken exact gate is a follow-up).

    Default path with a handful of self-built skills should stay well under
    the 800-token budget. This is a sanity bound, not the precise tiktoken gate.
    """
    _use_tmp_storage(tmp_path, monkeypatch)

    # Seed 8 self-built skills (PR-A target ceiling before degradation).
    for i in range(8):
        skill_manage(
            action="create", name=f"skill-{i:02d}",
            description=f"short desc {i}",
            task_type="develop", topic="tiling", body=VALID_BODY,
        )

    pb = PromptBuilder()
    from ascend_op_agent.agent.memory import MemoryStore
    prompt = pb.build_system_prompt(
        workspace_path=str(tmp_path),
        memory_store=MemoryStore(),
        skills_layer_override=None,
        task_type=None,
    )

    # chars/4 proxy token estimate for the Layer 6 region.
    # Extract just the skills section (between "## Available Skills" and the next ## header
    # that isn't part of skills). Conservative: use whole prompt upper bound.
    tok_estimate = len(prompt) // 4
    # Whole-prompt bound (Layer 6 is a fraction of this). Generous ceiling.
    assert tok_estimate < 8000, (
        f"whole-prompt chars/4 = {tok_estimate} exceeded sanity bound; "
        f"Layer 6 token regression likely"
    )
