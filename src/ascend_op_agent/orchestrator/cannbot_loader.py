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

import functools
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

CANNBOT_ROOT = (Path(__file__).parent.parent.parent.parent / "vendor" / "cannbot-skills").resolve()

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

    rest = content[len(_FRONTMATTER_DELIMITER) :]
    end_match = re.search(r"^---\s*$", rest, re.MULTILINE)
    if not end_match:
        return {}, content

    fm_text = rest[: end_match.start()]
    body = rest[end_match.end() :].lstrip("\n")
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


# PR-B U1: SKILL_BUNDLES (graph, phase) → (task_type, topic) 1:1 mapping.
# R5b 路由前置(R5b 用此查表把 PhaseRunner 的 phase 映射到 task_type/topic 过滤 self-built skill)。
# 沿用 PR-A U1 草案的 lossy 归类(design/review→kernel_pattern, codegen/compile_fix→build_env)
# 完整覆盖现有 7 bucket 让 R5b 路由可直接查表;lossy 归类 + 可追溯性。
CANBOT_BUNDLE_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("migration", "cuda_frontend"): ("migrate", "cuda_frontend"),
    ("migration", "triton_frontend"): ("migrate", "triton_frontend"),
    ("new_dev", "design"): ("develop", "kernel_pattern"),
    ("new_dev", "codegen"): ("develop", "build_env"),
    ("new_dev", "review"): ("develop", "kernel_pattern"),
    ("any", "compile_fix"): ("develop", "build_env"),
    ("any", "precision_fix"): ("develop", "precision"),
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


# ---- U1: list_cannbot_skill_names for R12 同名校验 ----


@functools.lru_cache(maxsize=1)
def _list_cannbot_skill_names_default() -> frozenset[str]:
    """``list_cannbot_skill_names`` 的默认 root 缓存版本。

    遍历 ``SKILL_BUNDLES`` 中所有 declared 路径(去重),用 ``load_skill`` 读
    ``frontmatter.name``。**进程内缓存**(``lru_cache(maxsize=1)``)+ 不落盘:
    SKILL_BUNDLES 静态,cannbot submodule 升级才会失效,届时进程重启即生效。

    失败处理(vendored submodule 未初始化 / SKILL.md 缺失 / frontmatter 无 name):
    全部跳过,不抛。返回 ``frozenset`` 便于 hash / set ops(U4 R12 fail-closed 决策)。

    U4 R12 fail-closed 用法:``names = list_cannbot_skill_names()``;若 vendored
    submodule 已配置但 names 为空 → reject self-built skill 写入;若 vendored
    未配置(CANNBOT_ROOT 不存在)→ warn 但允许。两者都依赖本函数"空 vs 非空"
    二态而不抛异常。
    """
    if not CANNBOT_ROOT.is_dir():
        # cannbot submodule 未初始化 → 返回空集
        return frozenset()

    # SKILL_BUNDLES values 嵌套 list,flatten 去重
    rel_paths: set[str] = set()
    for paths in SKILL_BUNDLES.values():
        for rel in paths:
            rel_paths.add(rel)

    names: set[str] = set()
    for rel in rel_paths:
        skill_dir = CANNBOT_ROOT / rel
        if not skill_dir.is_dir():
            continue
        try:
            skill = load_skill(skill_dir)
        except (FileNotFoundError, ValueError, OSError):
            # 缺失 SKILL.md / frontmatter 缺字段 / 读失败 → 跳过不抛
            continue
        if skill.name:
            names.add(skill.name)
    return frozenset(names)


def list_cannbot_skill_names(root: Path | str | None = None) -> set[str]:
    """列出 vendored cannbot skill 名集合(供 R12 同名校验)。

    行为契约:

    - vendored submodule 已初始化 + SKILL.md frontmatter 完整 → 返回非空 set
      (生产路径覆盖 7 个 phase bucket 的所有 skill 名)
    - vendored 路径缺失(CANNBOT_ROOT 不存在)或 SKILL.md 不可读 → 返回空 set,**不抛**
    - 二次调用缓存命中(``lru_cache(maxsize=1)``,默认 root 路径)
    - 测试可注入 ``root`` 参数(显式路径)跳过缓存以避免污染

    Args:
        root: 可选,显式 cannbot 根目录。生产代码传 None 用 ``CANNBOT_ROOT``;
            测试可传 ``tmp_path`` 跳过缓存走另一路径。

    Returns:
        所有可加载 cannbot skill 的 ``name`` 集合(``set[str]``,非 frozenset
        以便调用方做 union/difference)。空集代表"无可用 cannbot skill"。
    """
    if root is not None:
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            return set()
        names: set[str] = set()
        rel_paths: set[str] = set()
        for paths in SKILL_BUNDLES.values():
            for rel in paths:
                rel_paths.add(rel)
        for rel in rel_paths:
            skill_dir = root_path / rel
            if not skill_dir.is_dir():
                continue
            try:
                skill = load_skill(skill_dir)
            except (FileNotFoundError, ValueError, OSError):
                continue
            if skill.name:
                names.add(skill.name)
        return names

    # 默认路径 → 走 lru_cache 的 no-arg helper
    return set(_list_cannbot_skill_names_default())


# ---- U2: SkillUsageRegistry(signal-1 加载/使用跟踪) ----


@dataclass
class SkillLoad:
    """单次 phase 的 skill 加载/使用记录。

    loaded_skills: 该阶段从 SKILL_BUNDLES 表加载的 skill 名(graph 决定)
    used_skills: LLM 实际读了 skill 文件的(signal-1: file_read 路径匹配 cannbot root)
    """

    thread_id: str
    phase: str
    skill_names: list[str]
    used_skills: list[str]
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "phase": self.phase,
            "skill_names": list(self.skill_names),
            "used_skills": list(self.used_skills),
            "timestamp": self.timestamp,
        }


class SkillUsageRegistry:
    """线程安全的 skill 加载/使用注册表(单例,ephemeral —— P0 不持久化)。

    signal-1 only:file_read 工具调用且 path 匹配 ``CANNBOT_ROOT`` 视为"使用"。
    持久化到 checkpoint 是 P1 follow-up(plan R5 scope)。

    用法:
      - make_llm_node._node 跑完 LLM 后,扫 ``agent._tool_calls_log`` 抽 file_read
        path 匹配 cannbot root → record_use
      - record_load 记录该阶段 SKILL_BUNDLES 加载了哪些 skill(显式)
      - phase_callback("skill.usage", payload) 推前端实时显示
    """

    _instance: Optional["SkillUsageRegistry"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        # {thread_id: {phase: SkillLoad}}
        self._records: dict[str, dict[str, SkillLoad]] = {}
        self._lock = threading.Lock()

    @classmethod
    def instance(cls) -> "SkillUsageRegistry":
        """进程级单例。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def record_load(self, thread_id: str, phase: str, skill_names: list[str]) -> None:
        """记录某 phase 加载的 skill 名(显式,来自 SKILL_BUNDLES)。

        幂等:同 (thread_id, phase) 重复调用覆盖 skill_names,不重复 append。
        used_skills 保留已有值(record_use 之后调用 record_load 不会清空 used)。
        """
        if not thread_id or not phase:
            return
        with self._lock:
            phase_map = self._records.setdefault(thread_id, {})
            existing = phase_map.get(phase)
            if existing is None:
                phase_map[phase] = SkillLoad(
                    thread_id=thread_id,
                    phase=phase,
                    skill_names=list(skill_names),
                    used_skills=[],
                    timestamp=time.time(),
                )
            else:
                existing.skill_names = list(skill_names)

    def record_use(self, thread_id: str, phase: str, used_skills: list[str]) -> None:
        """记录 LLM 实际使用的 skill(signal-1: file_read 路径匹配)。

        若该 phase 未 record_load 过,自动建一个空 skill_names 的记录。
        """
        if not thread_id or not phase:
            return
        with self._lock:
            phase_map = self._records.setdefault(thread_id, {})
            existing = phase_map.get(phase)
            if existing is None:
                phase_map[phase] = SkillLoad(
                    thread_id=thread_id,
                    phase=phase,
                    skill_names=[],
                    used_skills=list(used_skills),
                    timestamp=time.time(),
                )
            else:
                existing.used_skills = list(used_skills)

    def get_loads(self, thread_id: str) -> list[SkillLoad]:
        """返回某 thread 的所有 phase 记录(按 phase 字典序)。"""
        with self._lock:
            phase_map = self._records.get(thread_id, {})
            return [phase_map[k] for k in sorted(phase_map.keys())]

    def clear(self, thread_id: Optional[str] = None) -> None:
        """清空(测试用)。thread_id=None 清全部。"""
        with self._lock:
            if thread_id is None:
                self._records.clear()
            else:
                self._records.pop(thread_id, None)


def extract_used_skills(
    tool_calls_log: list[dict], cannbot_root: Optional[Path] = None
) -> list[str]:
    """signal-1:从 agent._tool_calls_log 抽 LLM 实际读了哪些 skill。

    判定:file_read 工具调用,且 ``args.path`` 在 ``cannbot_root`` 子树内。
    skill 名取 path 相对 cannbot_root 的第一段(如 ``cuda2ascend-simt``)。

    去重保序。无匹配返回空 list。
    """
    root = Path(cannbot_root).resolve() if cannbot_root is not None else CANNBOT_ROOT
    used: list[str] = []
    seen: set[str] = set()
    for entry in tool_calls_log or []:
        if entry.get("name") != "file_read":
            continue
        path_str = (entry.get("args") or {}).get("path", "")
        if not path_str:
            continue
        try:
            p = Path(path_str).resolve()
        except (OSError, ValueError):
            continue
        if not _is_within(p, root):
            continue
        # 相对 root 的第一段 = skill 名(cuda2ascend-simt / triton-op-coding ...)
        try:
            rel = p.relative_to(root)
            skill_name = rel.parts[0] if rel.parts else ""
        except ValueError:
            continue
        if skill_name and skill_name not in seen:
            seen.add(skill_name)
            used.append(skill_name)
    return used


def render_skill_bundle_text(
    skills: list[CannbotSkill],
    phase: Optional[str] = None,
    inline_build_template: bool = False,
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
        inline_build_template: U2(codegen 阶段用)—— True 时从 codegen skill bundle
            中找 ``references/add_custom/`` 参考工程,内联其 CMakeLists.txt + run.sh
            (含正确 CANN 构建环境配置:ASCEND_CANN_PACKAGE_PATH 等)。
            解决 spike #4/#5 暴露的"LLM 写 build.sh 漏 CANN 环境变量"问题 ——
            skill 简介层不够,LLM 不主动调 skill_ops,故 codegen 阶段直接内联
            可编译参考工程的构建文件。默认 False(向后兼容,其他阶段不受影响)。

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

    # U2: inline_build_template —— codegen 阶段内联可编译参考工程的构建文件
    if inline_build_template:
        build_section = _render_build_template_section(skills)
        if build_section:
            lines.append("")
            lines.append(build_section)

    return "\n".join(lines)


def _render_build_template_section(skills: list[CannbotSkill]) -> str:
    """U2:从 codegen skill bundle 找 add_example 参考工程,内联其构建文件。

    add_example 是 ascendc-registry-invoke-template skill 下的完整可编译 add 算子
    工程(references/add_example/),显式含 ASCEND_COMPUTE_UNIT(arch22/arch35 分代),
    跟 910B CANN 9.1.0 的 legacy_modules/host_config.cmake 期望兼容。spike #6
    证明 add_custom(ascendc-direct-invoke-template/references/add_custom/)跟
    910B 9.1.0 不兼容(SOC_VERSION/ASCEND_CANN_PACKAGE_PATH 缺失)。

    只内联构建文件(CMakeLists.txt + build.sh),不内联 kernel/host 代码(那些
    LLM 按算子语义自己写),控制 context 大小。
    """
    # U3(强化 fix_loop):解耦查找 —— 不依赖入参 skills 的成员。codegen bundle 是
    # ascendc-direct-invoke-template + simt-best-practices,没有 add_example;
    # add_example 在 ascendc-registry-invoke-template/references/。原遍历逻辑
    # 在 codegen 阶段永远找不到 → 返空串(死代码)。显式定位 add_example,add_example
    # 是构建参考工程,与 codegen skill 简介是不同语义层,不污染 codegen SKILL_BUNDLES(省 token)。
    add_example_dir = (
        CANNBOT_ROOT / "ops" / "ascendc-registry-invoke-template" / "references" / "add_example"
    )
    if not add_example_dir.is_dir():
        # 路径缺失降级:返空串,不阻塞 codegen(等价现状)
        return ""
    _BUILD_FILES = ("CMakeLists.txt", "build.sh")
    parts: list[str] = [
        "## 构建参考(add_example 可编译工程,U3 显式定位, 910B CANN 9.1.0 兼容)",
        "",
        "以下是 references/add_example 的构建文件(显式 ASCEND_COMPUTE_UNIT,",
        "arch22/arch35 分代,910B 期望的 legacy_modules/host_config.cmake 期望)。",
        "生成 build.sh / CMakeLists.txt 时**以此为准**。",
        "",
    ]
    for fname in _BUILD_FILES:
        fpath = add_example_dir / fname
        if fpath.is_file():
            content = fpath.read_text(encoding="utf-8")
            lang = "cmake" if fname == "CMakeLists.txt" else "bash"
            parts.append(f"### {fname}")
            parts.append(f"```{lang}")
            parts.append(content.rstrip())
            parts.append("```")
            parts.append("")
    if len(parts) > 6:  # 至少内联了 1 个文件
        return "\n".join(parts)
    return ""


def _strip_opapi_section(content: str) -> str:
    """移除 op_host/CMakeLists.txt 的 op_api(aclnn) library 段 + package_add 引用。

    方向 B 不注入 op_api/(aclnn 封装 plan deferred),但 add_example 原版 op_host/CMakeLists
    引用 op_api/aclnn_*.cpp 构建 cust_opapi library,导致 CMake 'No SOURCES given to target
    cust_opapi'。compile(msopgen compile)只编译 kernel+host,op_api 是上层 aclnn 封装,
    删后不影响 kernel/host 编译。
    """
    # 删 set(op_api_dir...) 到 target_link_options(cust_opapi...) 整段
    content = re.sub(
        r"set\(op_api_dir[^\n]*\n.*?target_link_options\(cust_opapi[^\n]*\)\n",
        "",
        content,
        flags=re.DOTALL,
    )
    # 删 npu_op_package_add LIBRARY 列表里的 cust_opapi 行
    content = re.sub(r"\n\s*cust_opapi(?=\s*\n)", "", content)
    return content


def load_build_scaffold(op_snake: str, op_pascal: str) -> dict[str, str]:
    """U2 方向 B:从 vendor add_example 读 5 个构建文件,参数化 op 名,返回 {relpath: content}。

    构建文件(build scaffold,不经 LLM):根 CMakeLists.txt、build.sh、
    op_host/CMakeLists.txt、op_kernel/CMakeLists.txt、op_graph/CMakeLists.txt。
    参数化替换(先长串再短串,避免误替):
      add_example_custom -> {op_snake}_custom  (package_name)
      add_example_op_prj -> {op_snake}_op_prj  (project)
      AddExample         -> {op_pascal}        (类名/OP_TYPE)
      add_example        -> {op_snake}         (函数名/文件名)
    返回 {relpath: content},relpath 保留子目录(如 op_host/CMakeLists.txt)。
    路径缺失 warn + 跳过该文件(降级,不抛,不阻塞 codegen)。
    """
    import logging

    add_example_dir = (
        CANNBOT_ROOT / "ops" / "ascendc-registry-invoke-template" / "references" / "add_example"
    )
    if not add_example_dir.is_dir():
        logging.warning("load_build_scaffold: add_example dir missing: %s", add_example_dir)
        return {}
    _BUILD_FILES = (
        "CMakeLists.txt",
        "build.sh",
        "op_host/CMakeLists.txt",
        "op_kernel/CMakeLists.txt",
        "op_graph/CMakeLists.txt",
    )
    out: dict[str, str] = {}
    for rel in _BUILD_FILES:
        fpath = add_example_dir / rel
        if not fpath.is_file():
            logging.warning("load_build_scaffold: missing %s", fpath)
            continue
        content = fpath.read_text(encoding="utf-8")
        # 参数化替换(先长串再短串,避免 add_example 误替 add_example_custom 的前缀)
        content = content.replace("add_example_custom", f"{op_snake}_custom")
        content = content.replace("add_example_op_prj", f"{op_snake}_op_prj")
        content = content.replace("AddExample", op_pascal)
        content = content.replace("add_example", op_snake)
        if rel == "op_host/CMakeLists.txt":
            # 方向 B 不注入 op_api/(aclnn),移除 cust_opapi library 段避免 No SOURCES
            content = _strip_opapi_section(content)
        out[rel] = content
    return out


def load_semantic_examples() -> dict[str, list[tuple[str, str]]]:
    """加载 add_example 的 8 个语义文件(arch22 一套),按 codegen phase 分组。

    供 codegen 语义节点 prompt 内联范本**原文**:LLM 照抄 include 清单 + 宏结构 + API,
    把 add_example/AddExample 替换为 state.op_info.name/class_name,避免幻觉(实测 LLM
    误加 vector_add_tiling.h include —— add_example 范本不 include 它,autogen 也不生成)。
    不参数化(返回原文),由 prompt 指示 LLM 替换;路径缺失 warn 返空列表(降级,不阻塞)。
    """
    import logging

    add_example_dir = (
        CANNBOT_ROOT / "ops" / "ascendc-registry-invoke-template" / "references" / "add_example"
    )
    if not add_example_dir.is_dir():
        logging.warning("load_semantic_examples: add_example dir missing: %s", add_example_dir)
        return {}
    arch = "arch22"  # 910B-only
    groups: dict[str, list[str]] = {
        "codegen_kernel": [
            f"op_kernel/add_example_{arch}.cpp",
            f"op_kernel/{arch}/add_example.h",
            f"op_kernel/{arch}/add_example_tiling_data.h",
            f"op_kernel/{arch}/add_example_tiling_key.h",
        ],
        "codegen_host": [
            "op_host/add_example_def.cpp",
            "op_host/add_example_infershape.cpp",
            f"op_host/{arch}/add_example_tiling.cpp",
        ],
        "codegen_proto": [
            "op_graph/add_example_proto.h",
        ],
    }
    result: dict[str, list[tuple[str, str]]] = {}
    for phase, rels in groups.items():
        files: list[tuple[str, str]] = []
        for rel in rels:
            fpath = add_example_dir / rel
            if not fpath.is_file():
                logging.warning("load_semantic_examples: missing %s", fpath)
                continue
            files.append((rel, fpath.read_text(encoding="utf-8")))
        result[phase] = files
    return result
