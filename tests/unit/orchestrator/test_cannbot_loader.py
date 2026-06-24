"""U1 cannbot_loader 单测。

覆盖:
- load_skill 解析三个代表 skill(cuda2ascend-simt/triton-op-coding/ascendc-tiling-design)
- resolve_reference 解析 ``@references/*.md``(兼容单复数)
- 路径穿越防护
- build_skill_bundle 查决策表
- 错误路径:缺失 SKILL.md / 缺失 frontmatter 字段
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ascend_op_agent.orchestrator.cannbot_loader import (
    CANNBOT_ROOT,
    CannbotSkill,
    _parse_frontmatter,
    build_skill_bundle,
    load_skill,
)


REPRESENTATIVE_SKILLS = [
    "ops-lab/cuda2ascend-simt",
    "ops/triton-op-coding",
    "ops/ascendc-tiling-design",
]


@pytest.fixture(scope="module")
def cannbot_available() -> bool:
    return (CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt" / "SKILL.md").is_file()


@pytest.mark.parametrize("rel", REPRESENTATIVE_SKILLS)
def test_load_skill_representative(cannbot_available: bool, rel: str) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skill = load_skill(CANNBOT_ROOT / rel)
    assert isinstance(skill, CannbotSkill)
    assert skill.name, f"name empty for {rel}"
    assert skill.description, f"description empty for {rel}"
    assert skill.body, f"body empty for {rel}"
    assert skill.base_dir.is_dir()
    assert "name" in skill.frontmatter
    assert "description" in skill.frontmatter


def test_load_skill_cuda2ascend_specific_fields(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skill = load_skill(CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt")
    assert skill.name == "cuda2ascend-simt"
    assert "CUDA" in skill.description or "cuda" in skill.description.lower()
    assert "Ascend" in skill.body or "ascend" in skill.body.lower()


def test_resolve_reference_handles_references_plural(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skill = load_skill(CANNBOT_ROOT / "ops" / "triton-op-coding")
    refs = skill.list_references()
    if not refs:
        pytest.skip("triton-op-coding has no references/ dir in this pin")
    target = refs[0]
    rel = target.relative_to(skill.base_dir).as_posix()
    content = skill.resolve_reference(f"@{rel}")
    assert content is not None
    assert len(content) > 0


def test_resolve_reference_handles_reference_singular(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skill = load_skill(CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt")
    refs = skill.list_references()
    if not refs:
        pytest.skip("cuda2ascend-simt has no reference/ dir in this pin")
    target = refs[0]
    rel = target.relative_to(skill.base_dir).as_posix()
    content_via_at = skill.resolve_reference(f"@{rel}")
    content_bare = skill.resolve_reference(rel)
    assert content_via_at == target.read_text(encoding="utf-8")
    assert content_bare == content_via_at


def test_resolve_reference_bare_filename_searches_reference_dirs(
    cannbot_available: bool,
) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skill = load_skill(CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt")
    refs = skill.list_references()
    if not refs:
        pytest.skip("no reference files to exercise bare-name lookup")
    target = refs[0]
    content = skill.resolve_reference(target.name)
    assert content == target.read_text(encoding="utf-8")


def test_resolve_reference_rejects_path_traversal(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skill = load_skill(CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt")
    assert skill.resolve_reference("@../../etc/passwd") is None
    assert skill.resolve_reference("@/etc/passwd") is None
    assert skill.resolve_reference("@../../../SKILL.md") is None


def test_resolve_reference_missing_returns_none(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skill = load_skill(CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt")
    assert skill.resolve_reference("@references/__nonexistent__.md") is None
    assert skill.resolve_reference("__nonexistent__.md") is None
    assert skill.resolve_reference("") is None
    assert skill.resolve_reference("@") is None


def test_load_skill_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path / "no_such_dir")


def test_load_skill_missing_skill_md_raises(tmp_path: Path) -> None:
    d = tmp_path / "partial"
    d.mkdir()
    (d / "other.md").write_text("hello", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_skill(d)


def test_load_skill_missing_name_raises(tmp_path: Path) -> None:
    d = tmp_path / "no_name"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\ndescription: foo\n---\nbody", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="name"):
        load_skill(d)


def test_load_skill_missing_description_raises(tmp_path: Path) -> None:
    d = tmp_path / "no_desc"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: foo\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError, match="description"):
        load_skill(d)


def test_load_skill_no_frontmatter_degrades(tmp_path: Path) -> None:
    d = tmp_path / "no_fm"
    d.mkdir()
    (d / "SKILL.md").write_text("# plain markdown without frontmatter", encoding="utf-8")
    with pytest.raises(ValueError):
        load_skill(d)


def test_parse_frontmatter_extracts_dict_and_body() -> None:
    content = "---\nname: foo\ndescription: bar\n---\nbody line 1\nbody line 2\n"
    fm, body = _parse_frontmatter(content)
    assert fm == {"name": "foo", "description": "bar"}
    assert body.startswith("body line 1")


def test_parse_frontmatter_no_frontmatter_returns_empty() -> None:
    fm, body = _parse_frontmatter("# just markdown\n")
    assert fm == {}
    assert body.startswith("# just markdown")


def test_parse_frontmatter_only_opening_delimiter_returns_empty() -> None:
    fm, body = _parse_frontmatter("---\nname: foo\nbut no closing\n")
    assert fm == {}
    assert "no closing" in body


def test_parse_frontmatter_non_dict_yaml_returns_empty() -> None:
    fm, body = _parse_frontmatter("---\n- a\n- b\n---\nbody\n")
    assert fm == {}


def test_build_skill_bundle_migration_cuda(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skills = build_skill_bundle("cuda_frontend", graph="migration")
    names = [s.name for s in skills]
    assert "cuda2ascend-simt" in names


def test_build_skill_bundle_migration_triton_chain_has_five(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skills = build_skill_bundle("triton_frontend", graph="migration")
    names = {s.name for s in skills}
    expected_subset = {
        "triton-op-coding",
        "triton-op-designer",
        "triton-op-verifier",
        "triton-latency-optimizer",
        "triton-task-extractor",
    }
    assert expected_subset.issubset(names), f"missing: {expected_subset - names}"


def test_build_skill_bundle_unknown_key_returns_empty(cannbot_available: bool) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    assert build_skill_bundle("__unknown_phase__", graph="any") == []


def test_build_skill_bundle_skips_missing_paths_gracefully(
    cannbot_available: bool, tmp_path: Path
) -> None:
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")
    skills = build_skill_bundle("cuda_frontend", graph="migration", root=tmp_path)
    assert skills == []
