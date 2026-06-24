"""cannbot-skills 加载器。

消费华为官方 cannbot-skills(https://gitcode.com/cann/cannbot-skills)作知识层。
本加载器:

1. 解析 SKILL.md 的 YAML frontmatter(name/description/body/base_dir)
2. 保留 ``@references/*.md`` 相对路径引用的解析能力(兼容 reference/references 单复数,
   cannbot 仓库内两种命名都存在:cuda2ascend-simt 用单数,triton-op-coding 用复数)
3. 根据 ``(phase, graph)`` 查决策表构建 skill bundle —— hybrid 集成的编排层入口,
   编排层显式映射,phase 内 LLM 读 skill ``description`` 触发词再路由

非目标:不重写 cannbot skills,不解析 cannbot 的 plugin/agent persona 格式(后续 U9 再加)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

CANNBOT_ROOT = (
    Path(__file__).parent.parent.parent.parent / "vendor" / "cannbot-skills"
).resolve()

_REFERENCE_DIR_CANDIDATES = ("references", "reference")
_FRONTMATTER_DELIMITER = "---"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter + body。

    无 frontmatter 则返回 ``({}, content)``。frontmatter 非字典(如 YAML 解析成 list)
    也返回空 dict,由调用方校验必填字段。
    """
    if not content.startswith(_FRONTMATTER_DELIMITER):
        return {}, content

    rest = content[len(_FRONTMATTER_DELIMITER):]
    end_match = re.search(r"^---\s*$", rest, re.MULTILINE)
    if not end_match:
        return {}, content

    fm_text = rest[: end_match.start()]
    body = rest[end_match.end():].lstrip("\n")
    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        return {}, content
    return fm, body


@dataclass
class CannbotSkill:
    """cannbot SKILL.md 制品。

    封装 frontmatter + body + base_dir,支持 ``@references/*.md`` 相对路径解析。
    base_dir 即 SKILL.md 所在目录,resolve_reference 在此目录下查找。
    """

    name: str
    description: str
    body: str
    base_dir: Path
    frontmatter: dict = field(default_factory=dict)

    def resolve_reference(self, ref: str) -> Optional[str]:
        """解析 ``@references/foo.md`` 形式的相对路径引用。

        支持以下 ref 形式:

        - ``"@references/foo.md"`` / ``"@reference/foo.md"``(去 ``@`` 后相对 base_dir,单复数兼容)
        - ``"references/foo.md"``(无 ``@`` 前缀)
        - ``"foo.md"``(裸文件名,先尝试 ``references/`` 再 ``reference/`` 下找)

        路径穿越(如 ``@../../etc/passwd``)返回 None。
        """
        path_str = ref
        if path_str.startswith("@"):
            path_str = path_str[1:]
        if path_str.startswith("./"):
            path_str = path_str[2:]
        path_str = path_str.lstrip("/")
        if not path_str:
            return None

        candidate = (self.base_dir / path_str).resolve()
        if _is_within(candidate, self.base_dir) and candidate.is_file():
            return candidate.read_text(encoding="utf-8")

        if "/" not in path_str:
            for sub in _REFERENCE_DIR_CANDIDATES:
                candidate = (self.base_dir / sub / path_str).resolve()
                if _is_within(candidate, self.base_dir) and candidate.is_file():
                    return candidate.read_text(encoding="utf-8")

        return None

    def list_references(self) -> list[Path]:
        """列出 skill 目录下所有可引用的 reference 文件(单复数目录都扫)。"""
        out: list[Path] = []
        for sub in _REFERENCE_DIR_CANDIDATES:
            d = self.base_dir / sub
            if d.is_dir():
                for p in d.rglob("*"):
                    if p.is_file():
                        out.append(p)
        return out


def load_skill(skill_dir: Path | str) -> CannbotSkill:
    """加载 cannbot skill 目录。

    Args:
        skill_dir: skill 目录路径(必须含 ``SKILL.md``)

    Returns:
        CannbotSkill 实例

    Raises:
        FileNotFoundError: ``SKILL.md`` 不存在
        ValueError: frontmatter 缺失 ``name`` 或 ``description``
    """
    skill_dir = Path(skill_dir).resolve()
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")

    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(content)

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name:
        raise ValueError(f"Missing 'name' in frontmatter: {skill_md}")
    if not description:
        raise ValueError(f"Missing 'description' in frontmatter: {skill_md}")

    return CannbotSkill(
        name=str(name),
        description=str(description),
        body=body,
        base_dir=skill_dir,
        frontmatter=frontmatter,
    )


SKILL_BUNDLES: dict[tuple[str, str], list[str]] = {
    ("migration", "cuda_frontend"): [
        "ops-lab/cuda2ascend-simt",
    ],
    ("migration", "triton_frontend"): [
        "ops/triton-op-coding",
        "ops/triton-op-designer",
        "ops/triton-op-verifier",
        "ops/triton-latency-optimizer",
        "ops/triton-task-extractor",
    ],
    ("new_dev", "design"): [
        "ops/ascendc-tiling-design",
        "ops/ascendc-simt-tiling-design",
        "ops/npu-arch",
    ],
    ("new_dev", "codegen"): [
        "ops/ascendc-direct-invoke-template",
        "ops/ascendc-simt-best-practices",
    ],
    ("new_dev", "review"): [
        "ops/ascendc-code-review",
    ],
    ("any", "compile_fix"): [
        "ops/ascendc-crash-debug",
        "ops/ascendc-runtime-debug",
    ],
    ("any", "precision_fix"): [
        "ops/ascendc-precision-debug",
        "ops/pypto-precision-compare",
    ],
}


def build_skill_bundle(
    phase: str,
    graph: str = "any",
    root: Path | str | None = None,
) -> list[CannbotSkill]:
    """根据 ``(phase, graph)`` 查决策表,加载对应 cannbot skill bundle。

    hybrid 集成的编排层入口:返回的 skill bundle 注入到 PromptBuilder Layer 6
    作为该阶段的 cannbot 知识上下文。phase 内 LLM 读 ``description`` 触发词再路由。

    缺失的 skill 路径优雅跳过(不抛错)—— cannbot submodule 升级后某些路径可能改名。
    """
    root_path = Path(root).resolve() if root is not None else CANNBOT_ROOT
    paths = SKILL_BUNDLES.get((graph, phase), [])

    skills: list[CannbotSkill] = []
    for rel in paths:
        skill_dir = root_path / rel
        if not skill_dir.is_dir():
            continue
        try:
            skills.append(load_skill(skill_dir))
        except (FileNotFoundError, ValueError):
            continue
    return skills


def render_skill_bundle_text(
    skills: list[CannbotSkill],
    phase: Optional[str] = None,
) -> str:
    """把 skill bundle 渲染成 PromptBuilder Layer 6 文本。

    格式(每个 skill 含 name + description,不 dump body 避免上下文爆炸;
    LLM 需要细节时由 ``skill_ops`` 工具按名加载 —— cannbot 设计如此):

        ## Available Skills (phase=design)

        - **ascendc-tiling-design**: <description>
        - **ascendc-simt-tiling-design**: <description>
        ...

        使用 skill_ops 工具加载完整 skill 内容。

    Args:
        skills: ``build_skill_bundle`` 返回的 skill 列表
        phase: 当前阶段名(仅用于标题展示);None 时不显示

    Returns:
        Layer 6 文本。空列表返回空串(调用方决定是否降级为默认 Layer 6)
    """
    if not skills:
        return ""

    lines: list[str] = []
    if phase is not None:
        lines.append(f"## Available Skills (phase={phase})")
    else:
        lines.append("## Available Skills")

    lines.append("")
    for s in skills:
        # description 可能多行,首行作 summary,其余忽略
        desc_first = s.description.strip().split("\n", 1)[0]
        lines.append(f"- **{s.name}**: {desc_first}")

    lines.append("")
    lines.append(
        "需要详细 skill 内容时,使用 skill_ops 工具按名加载,或读取 skill 目录下"
        "的 SKILL.md / references/ 文件。"
    )
    return "\n".join(lines)
