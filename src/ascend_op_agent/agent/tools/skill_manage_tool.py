# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""skill_manage agent tool — 7 actions for self-built skill management (PR-A U4).

Actions:
  create         validate R2 + save new skill to self-built/{name}/SKILL.md
  patch          full-replacement save (no diff/merge in PR-A)
  add_reference  attach a reference doc to an existing skill (R13)
  archive        move SKILL.md to .archived/ + inject archive_at/archive_reason
  load           read SKILL.md + parse frontmatter
  list_skills    enumerate self-built skills
  search         frontmatter-scan filter (U3 FTS5 deferred to PR-B)

The 7 actions live behind a single tool (KTD-7): one Click/ToolRegistry entry,
one JSON-schema parameters object, action enum discriminated internally. This
keeps L3 fork callers' (V2 plan) invocation surface stable.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ascend_op_agent.skills.models import Skill
from ascend_op_agent.skills.storage import SkillStorage

logger = logging.getLogger(__name__)


# Public constants ------------------------------------------------------------

TASK_TYPES = ("migrate", "analyze", "optimize", "develop")
ACTIONS = ("create", "patch", "add_reference", "archive", "load", "list_skills", "search")

# name regex: kebab-case, leading letter, trailing alphanumeric, lowercase only.
# Forbidden tokens guard against accidental PR-numbers / dates / task-objects.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")
_FORBIDDEN_NAME_TOKENS = ("pr-", "pr_", "_pr_", "-pr-", "test-pr-", "date-20", "issue-")

# Per KTD-2 alignment with rendering layer; authors should keep <=40 chars;
# the tool warns but does not block above this threshold (description payload
# is the storage source-of-truth even if Layer 6 truncates at render).
DESCRIPTION_MAX_LEN = 40
SOFT_WARN = "warning"  # response key for soft-violations

# Self-built layout lives under <skills_dir>/self-built/{name}/ — U4 F4 fix.
SELF_BUILT_SUBDIR = "self-built"


# R16 structured error --------------------------------------------------------


class SkillManageError(Exception):
    """Structured error returned by every skill_manage action (R16).

    Response shape:
        {
            "success": False,
            "error": "<code>",
            "field": "<name>",
            "reason": "<msg>",
            "remediation_hint": "<how to fix>",
            "warning": "<soft-warn string, only for soft-warn cases>" (optional)
        }
    """

    def __init__(
        self,
        code: str,
        field: str = "",
        reason: str = "",
        remediation_hint: str = "",
    ):
        self.code = code
        self.field = field
        self.reason = reason
        self.remediation_hint = remediation_hint
        super().__init__(f"[{code}] {field}: {reason}")

    def to_dict(self) -> dict[str, str]:
        out: dict[str, str] = {
            "success": False,
            "error": self.code,
            "field": self.field,
            "reason": self.reason,
            "remediation_hint": self.remediation_hint,
        }
        return out


# Action arg dataclasses ----------------------------------------------------


@dataclass
class CreateSkillArgs:
    name: str
    description: str
    task_type: str
    topic: str
    body: str


@dataclass
class PatchSkillArgs:
    name: str
    description: str
    task_type: str
    topic: str
    body: str


@dataclass
class AddReferenceArgs:
    skill_name: str
    reference_name: str
    reference_path: str


@dataclass
class ArchiveSkillArgs:
    skill_name: str
    archive_reason: str


@dataclass
class LoadSkillArgs:
    skill_name: str


@dataclass
class ListSkillsArgs:
    pass


@dataclass
class SearchSkillsArgs:
    query: Optional[str] = None
    task_type: Optional[str] = None
    topic: Optional[str] = None


# R2 static validation -------------------------------------------------------


def _validate_skill_static(args: CreateSkillArgs | PatchSkillArgs) -> list[str]:
    """Return list of soft-warn strings; raise SkillManageError on hard violations.

    Hard rules:
      (a) required: name, description, task_type, topic, body
      (b) name matches ^[a-z][a-z0-9-]*[a-z0-9]$ and contains no PR/error/date tokens
      (c) task_type ∈ TASK_TYPES
      (d) topic soft-warn only (KTD-3: free-form in PR-A)
      (e) first body paragraph is `## Project Scope`
      (f) cannbot 同名 fail-closed (R12) — handled in `_cannbot_name_check`, separate
    """
    warnings: list[str] = []

    if not _NAME_RE.match(args.name):
        raise SkillManageError(
            "validation_failed",
            "name",
            f"Name {args.name!r} must match ^[a-z][a-z0-9-]*[a-z0-9]$",
            "Use kebab-case starting with a letter and ending alphanumeric; "
            "only [a-z0-9-] allowed mid-string.",
        )
    bad_token = next((t for t in _FORBIDDEN_NAME_TOKENS if t in args.name), None)
    if bad_token is not None:
        raise SkillManageError(
            "validation_failed",
            "name",
            f"Name {args.name!r} contains forbidden token {bad_token!r} "
            "(PR numbers, dates, task objects belong in metadata, not name).",
            "Rename without the forbidden token. Traceability lives in frontmatter metadata.",
        )

    if args.task_type not in TASK_TYPES:
        raise SkillManageError(
            "validation_failed",
            "task_type",
            f"task_type {args.task_type!r} not in TASK_TYPES {list(TASK_TYPES)}",
            f"Set task_type to one of {list(TASK_TYPES)}.",
        )

    # Description length — soft-warn above 40 (KTD-2); never block here.
    if len(args.description) > DESCRIPTION_MAX_LEN:
        warnings.append(
            f"description length {len(args.description)} > {DESCRIPTION_MAX_LEN}; "
            "Layer 6 will truncate to 40 chars at render. Trim for cleaner prompt."
        )

    body = (args.body or "").strip()
    if not body:
        raise SkillManageError(
            "validation_failed",
            "body",
            "body is empty",
            "Provide the full SKILL.md body (must start with `## Project Scope`).",
        )
    first_line = body.split("\n", 1)[0].strip()
    if not first_line.startswith("## Project Scope"):
        raise SkillManageError(
            "validation_failed",
            "body",
            f"First body section must be '## Project Scope' (got {first_line!r})",
            "Begin the body with '## Project Scope' describing applicability and "
            "hardware/version boundaries.",
        )

    return warnings


def _cannbot_name_check(name: str) -> list[str]:
    """R12 cannbot 同名 fail-closed. Returns soft-warn strings (not hard errors).

    Behavior:
      • CANNBOT_ROOT not configured + list_cannbot_skill_names unavailable:
          warn, proceed (avoid blocking during U1 ramp-up)
      • CANNBOT_ROOT configured + cannbot list returns empty (submodule unreadable):
          *Hard reject* the write (R12 fail-closed per plan KTD-2 / R12)
      • Name collides with a cannbot name:
          *Hard reject*
      • Otherwise: no warning, proceed.
    """
    warnings: list[str] = []

    try:
        from ascend_op_agent.orchestrator.cannbot_loader import (
            CANNBOT_ROOT,
            list_cannbot_skill_names,
        )
    except ImportError:
        warnings.append(
            "list_cannbot_skill_names unavailable (orchestrator import failed); "
            "R12 not enforced this run."
        )
        return warnings

    if CANNBOT_ROOT is None:
        warnings.append("CANNBOT_ROOT not configured; skipping cannbot-name collision check.")
        return warnings

    # Always re-scan with explicit root (bypasses ``_list_cannbot_skill_names_default``
    # lru_cache, which can hold stale empty results from prior tests/processes and
    # would false-trigger r12_fail_closed here).
    try:
        names = list_cannbot_skill_names(root=CANNBOT_ROOT)
    except Exception as e:  # defensive — should not raise per U1 contract
        logger.warning("list_cannbot_skill_names raised %r; treating as unavailable", e)
        names = set()

    if not names:
        # Configured but empty: fail-closed (R12 strict).
        raise SkillManageError(
            "r12_fail_closed",
            "name",
            "cannbot submodule unavailable — refusing write to avoid "
            "cannbot-name collision risk (run `git submodule update --init`).",
            "Initialize vendored cannbot-skills or unset CANNBOT_ROOT to disable "
            "R12 enforcement; the latter is a developer-only warning path.",
        )
    if name in names:
        raise SkillManageError(
            "r12_collision",
            "name",
            f"Skill name {name!r} collides with a cannbot-owned skill name.",
            f"Pick a different name. Cannbot-owned names include: " f"{sorted(names)[:5]}…",
        )
    return warnings


def _provenance(write_origin: str = "manual") -> dict[str, Any]:
    """R4 + R10: write provenance + task_type + topic as flat metadata keys.

    Skill metadata is stored as a single-level dict so ``SkillStorage._build_skill_content``
    renders each key on its own YAML line; nested dicts would stringify as Python repr
    (see git history 2026-07-13).
    """
    return {
        "write_origin": write_origin,
        "written_by": "skill_manage",
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# Action handlers ------------------------------------------------------------


def _save_self_built(
    args: "CreateSkillArgs | PatchSkillArgs",
    is_update: bool,
) -> dict[str, Any]:
    """Shared create/patch path: R2 validation + R12 cannbot check + provenance + save.

    - create (``is_update=False``): new skill; no existence requirement.
    - patch  (``is_update=True``):  full-replacement; skill must already exist;
      ``updated_at`` stamped. Patch's body-required check uses a patch-specific
      message (more informative than the generic body-empty rule in
      ``_validate_skill_static``), so it runs first.
    """
    if is_update and not (args.body or "").strip():
        raise SkillManageError(
            "validation_failed",
            "body",
            "patch requires full body (full-replacement semantics), not partial merge.",
            "Pass the complete body content; this action overwrites the previous version entirely.",
        )

    warnings = _validate_skill_static(args)
    warnings += _cannbot_name_check(args.name)

    storage = SkillStorage()
    if is_update and not storage.skill_exists(args.name):
        raise SkillManageError(
            "not_found",
            "name",
            f"Cannot patch: skill {args.name!r} does not exist (use create instead).",
            "Use action='create' to write a new skill, or action='load' to inspect existing state.",
        )

    metadata = _provenance()
    metadata["task_type"] = args.task_type
    metadata["topic"] = args.topic
    if is_update:
        metadata["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    skill = Skill(
        name=args.name,
        description=args.description,
        content=args.body,
        metadata=metadata,
    )
    saved_path = storage.save_skill(skill, dimension="self_built")

    out: dict[str, Any] = {"success": True, "data": {"saved_to": saved_path, "name": args.name}}
    if warnings:
        out[SOFT_WARN] = warnings
    return out


def _action_create(args: CreateSkillArgs) -> dict[str, Any]:
    return _save_self_built(args, is_update=False)


def _action_patch(args: PatchSkillArgs) -> dict[str, Any]:
    return _save_self_built(args, is_update=True)


def _action_add_reference(args: AddReferenceArgs) -> dict[str, Any]:
    """R13: attach a reference doc under {skill_name}_reference/ as a SKILL.md with body = reference content."""
    storage = SkillStorage()
    if not storage.skill_exists(args.skill_name):
        raise SkillManageError(
            "not_found",
            "skill_name",
            f"Skill {args.skill_name!r} not found; create it before adding references.",
            "Create the parent skill first via action='create'.",
        )
    src = Path(args.reference_path)
    if not src.is_file():
        raise SkillManageError(
            "validation_failed",
            "reference_path",
            f"reference_path {src!r} is not an existing file.",
            "Provide an absolute or relative path to an existing file on disk.",
        )

    body = (
        f"# Reference: {args.reference_name}\n\n"
        f"Source path: {src}\n\n---\n\n"
        f"{src.read_text(encoding='utf-8', errors='replace')}"
    )
    metadata = _provenance()
    metadata["task_type"] = "reference"
    metadata["topic"] = args.reference_name
    metadata["parent_skill"] = args.skill_name
    metadata["reference_name"] = args.reference_name

    # Skill name = parent name; storage applies "_reference" suffix on save.
    ref_skill = Skill(
        name=args.skill_name,
        description=f"Reference for {args.skill_name}: {args.reference_name}",
        content=body,
        metadata=metadata,
    )
    saved_path = storage.save_skill(ref_skill, dimension="reference")
    return {
        "success": True,
        "data": {
            "saved_to": saved_path,
            "reference": args.reference_name,
            "parent_skill": args.skill_name,
        },
    }


def _action_archive(args: ArchiveSkillArgs) -> dict[str, Any]:
    storage = SkillStorage()
    loaded = storage.load_skill(args.skill_name)
    if loaded is None:
        raise SkillManageError(
            "not_found",
            "skill_name",
            f"Skill {args.skill_name!r} not found.",
            "Cannot archive what does not exist.",
        )

    skill_dir = Path(loaded.local_path)  # <skills_dir>/self-built/<name>
    archived_dir = skill_dir / ".archived"  # per-skill archive folder
    archived_dir.mkdir(parents=True, exist_ok=True)
    target = archived_dir / "SKILL.md"

    metadata = dict(loaded.metadata) if loaded.metadata else {}
    metadata["archive_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metadata["archive_reason"] = args.archive_reason
    metadata["archived_by"] = "skill_manage"

    augmented = Skill(
        name=loaded.name,
        description=loaded.description,
        content=loaded.content,
        tags=loaded.tags,
        version=loaded.version,
        author=loaded.author,
        platforms=loaded.platforms,
        prerequisites=loaded.prerequisites,
        metadata=metadata,
    )
    storage.save_skill(augmented, dimension="self_built")
    # Move the SKILL.md (with frontmatter edits applied) into .archived/ (per-skill)
    skill_path = skill_dir / "SKILL.md"
    if skill_path.exists():
        skill_path.rename(target)

    return {"success": True, "data": {"archived_to": str(target), "skill": args.skill_name}}


def _action_load(args: LoadSkillArgs) -> dict[str, Any]:
    storage = SkillStorage()
    loaded = storage.load_skill(args.skill_name)
    if loaded is None:
        raise SkillManageError(
            "not_found",
            "skill_name",
            f"Skill {args.skill_name!r} not found.",
            "Verify the name. Use action='list_skills' to enumerate available skills.",
        )
    return {"success": True, "data": loaded.to_dict()}


def _action_list_skills(_: ListSkillsArgs) -> dict[str, Any]:
    storage = SkillStorage()
    names = storage.list_skills()  # now includes self-built/*/ via storage refactor
    out: list[dict] = []
    for name in names:
        loaded = storage.load_skill(name)
        if loaded is None:
            continue
        meta = (loaded.metadata or {}).get("ascend_op_agent", {})
        out.append(
            {
                "name": name,
                "description": loaded.description,
                "task_type": meta.get("task_type"),
                "topic": meta.get("topic"),
            }
        )
    return {"success": True, "data": out}


def _make_skill_index() -> Any:
    """PR-B U2 helper: lazy SkillIndex factory (allows tests to monkeypatch).

    Kept module-level so tests can patch ``skill_manage_tool._make_skill_index``
    to inject a tmp-index without going through sentence-transformers loads.
    """
    from ascend_op_agent.skills.index import SkillIndex

    return SkillIndex()


def _action_search(args: SearchSkillsArgs) -> dict[str, Any]:
    """PR-B U2/R6: FTS5+ChromaDB hybrid search (复用 SkillsIndex.hybrid_search),
    支持 task_type/topic 过滤。Fallback: SkillsIndex 不可用 / 结果为空 → frontmatter scan(PR-A 行为)。

    Routing:
      - query 非空  → SkillsIndex.hybrid_search(query, k, task_type, topic)
      - query 空 + task_type → SkillsIndex.search_by_task_type() + 内存 topic 过滤
      - query 空 + 仅 topic (或全空) → frontmatter fallback(直接读 metadata)
      - SkillsIndex unavailable or empty result → frontmatter fallback (PR-A compat)
    """
    # Build a SkillIndex (lazy). 若 instantiation 失败(LLM/embedding 依赖)→ fall back.
    try:
        index = _make_skill_index()
    except Exception as e:  # noqa: BLE001 - 防御
        logger.warning("SkillsIndex unavailable, falling back to frontmatter scan: %r", e)
        return _action_search_frontmatter_fallback(args)

    query = (args.query or "").strip()

    if query:
        # 文本搜索:走 hybrid_search(FTS5 BM25 + Chroma 向量融合,alpha=0.4)
        try:
            skills = index.hybrid_search(
                query=query,
                k=10,
                task_type=args.task_type,
                topic=args.topic,
            )
        except TypeError:
            # 兼容旧 SkillIndex 签名(无 task_type/topic 参数)
            skills = index.hybrid_search(query=query, k=10)
        # SkillsIndex 结果为空 → frontmatter 兜底(保留 PR-A frontmatter-scan 兼容性)
        if not skills and (args.task_type or args.topic):
            return _action_search_frontmatter_fallback(args)
    elif args.task_type:
        # 空 query + task_type 过滤(R5b 路由命中后的列表渲染)
        skills = index.search_by_task_type(args.task_type)
        # search_by_task_type 不带 topic 字段 → frontmatter 二次过滤
        if args.topic:
            storage = SkillStorage()
            filtered: list[Any] = []
            for s in skills:
                loaded = storage.load_skill(s.name)
                meta = (loaded.metadata or {}).get("ascend_op_agent", {}) if loaded else {}
                if meta.get("topic") == args.topic:
                    filtered.append(s)
            skills = filtered
        # SkillsIndex 结果为空 → frontmatter 兜底(PR-A 兼容:
        # 旧 self-built skills 没注册进 SkillsIndex 也能 search 找得到)
        if not skills:
            return _action_search_frontmatter_fallback(args)
    else:
        # 空 query 无 task_type:topic-only / 全空查询 → frontmatter fallback
        return _action_search_frontmatter_fallback(args)

    # 渲染响应(从 frontmatter metadata 取 task_type/topic,与 list_skills 对齐)
    results: list[dict] = []
    for s in skills:
        storage = SkillStorage()
        loaded = storage.load_skill(s.name)
        meta = (loaded.metadata or {}).get("ascend_op_agent", {}) if loaded else {}
        results.append(
            {
                "name": s.name,
                "description": s.description,
                "task_type": meta.get("task_type"),
                "topic": meta.get("topic"),
            }
        )
    return {"success": True, "data": results}


def _action_search_frontmatter_fallback(args: SearchSkillsArgs) -> dict[str, Any]:
    """PR-A frontmatter scan fallback (SkillsIndex 不可用 或 topic-only 查询时使用)."""
    storage = SkillStorage()
    names = storage.list_skills()
    results: list[dict] = []
    for name in names:
        loaded = storage.load_skill(name)
        if loaded is None:
            continue
        meta = (loaded.metadata or {}).get("ascend_op_agent", {})
        if args.task_type is not None and meta.get("task_type") != args.task_type:
            continue
        if args.topic is not None and meta.get("topic") != args.topic:
            continue
        # 文本 query 在 frontmatter 路径下做"name/description LIKE"过滤(仅退化路径)
        q = (args.query or "").strip().lower()
        if q:
            haystack = " ".join(
                [
                    name.lower(),
                    (loaded.description or "").lower(),
                    " ".join(loaded.tags or []).lower(),
                ]
            )
            if q not in haystack:
                continue
        results.append(
            {
                "name": loaded.name,
                "description": loaded.description,
                "task_type": meta.get("task_type"),
                "topic": meta.get("topic"),
            }
        )
    return {"success": True, "data": results}


# Tool entry ------------------------------------------------------------------


def skill_manage(
    action: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    task_type: Optional[str] = None,
    topic: Optional[str] = None,
    body: Optional[str] = None,
    skill_name: Optional[str] = None,
    archive_reason: Optional[str] = None,
    reference_path: Optional[str] = None,
    reference_name: Optional[str] = None,
    search_query: Optional[str] = None,
    search_task_type: Optional[str] = None,
    search_topic: Optional[str] = None,
) -> dict[str, Any]:
    """7-action skill manager. Always returns a dict with `success` (R16).

    PR-B U2: ``search`` accepts ``search_query`` (text) in addition to the
    task_type/topic filters, routing through SkillsIndex.hybrid_search (FTS5
    BM25 + ChromaDB vector fusion; alpha=0.4). If SkillsIndex fails to
    instantiate (e.g. missing embedding deps), falls back to frontmatter scan.
    """
    try:
        if action == "create":
            return _action_create(
                CreateSkillArgs(
                    name=name or "",
                    description=description or "",
                    task_type=task_type or "",
                    topic=topic or "",
                    body=body or "",
                )
            )
        if action == "patch":
            return _action_patch(
                PatchSkillArgs(
                    name=name or "",
                    description=description or "",
                    task_type=task_type or "",
                    topic=topic or "",
                    body=body or "",
                )
            )
        if action == "add_reference":
            return _action_add_reference(
                AddReferenceArgs(
                    skill_name=skill_name or "",
                    reference_name=reference_name or "",
                    reference_path=reference_path or "",
                )
            )
        if action == "archive":
            return _action_archive(
                ArchiveSkillArgs(
                    skill_name=skill_name or "",
                    archive_reason=archive_reason or "no reason provided",
                )
            )
        if action == "load":
            return _action_load(LoadSkillArgs(skill_name=skill_name or ""))
        if action == "list_skills":
            return _action_list_skills(ListSkillsArgs())
        if action == "search":
            return _action_search(
                SearchSkillsArgs(
                    query=search_query,
                    task_type=search_task_type,
                    topic=search_topic,
                )
            )
        raise SkillManageError(
            "unknown_action",
            "action",
            f"Unknown action {action!r}.",
            f"action must be one of: {', '.join(ACTIONS)}.",
        )
    except SkillManageError as e:
        return e.to_dict()
    except Exception as e:  # last-resort safety net
        logger.exception("skill_manage unexpected failure")
        return {
            "success": False,
            "error": "unexpected_failure",
            "field": "",
            "reason": str(e),
            "remediation_hint": "Re-run with a smaller input or report a bug.",
        }


def register(registry) -> None:
    """Register skill_manage with the project's ToolRegistry.

    Schema is hand-written (not inferred) so the action enum is documented
    for LLM callers and the optional params are clear.
    """
    registry.register(
        name="skill_manage",
        description=(
            "Manage self-built skills living under "
            "~/.ascend_op_agent/skills/self-built/{name}/SKILL.md. "
            "7 actions: create | patch (full-replacement) | add_reference | "
            "archive | load | list_skills | search (PR-B U2 hybrid: FTS5 "
            "BM25 + ChromaDB fusion via SkillsIndex). Falls back to "
            "frontmatter scan if SkillsIndex is unavailable."
        ),
        func=skill_manage,
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": "Which skill_manage action to perform.",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name (create/patch). Must match ^[a-z][a-z0-9-]*[a-z0-9]$.",
                },
                "description": {
                    "type": "string",
                    "description": "Skill description (create/patch). Soft-warn above 40 chars.",
                },
                "task_type": {
                    "type": "string",
                    "enum": list(TASK_TYPES),
                    "description": "migrate | analyze | optimize | develop.",
                },
                "topic": {"type": "string", "description": "Free-form topic label (PR-A)."},
                "body": {
                    "type": "string",
                    "description": "Full SKILL.md body starting with `## Project Scope`.",
                },
                "skill_name": {
                    "type": "string",
                    "description": "Skill name (add_reference / archive / load).",
                },
                "archive_reason": {"type": "string", "description": "Reason string (archive)."},
                "reference_path": {
                    "type": "string",
                    "description": "Path to a reference file (add_reference).",
                },
                "reference_name": {
                    "type": "string",
                    "description": "Name for the reference (add_reference).",
                },
                "search_query": {
                    "type": "string",
                    "description": (
                        "Free-text query for hybrid search (FTS5 BM25 + "
                        "ChromaDB fusion). Empty + task_type → list by type."
                    ),
                },
                "search_task_type": {
                    "type": "string",
                    "description": "Filter search by task_type.",
                },
                "search_topic": {"type": "string", "description": "Filter search by topic."},
            },
            "required": ["action"],
        },
    )
