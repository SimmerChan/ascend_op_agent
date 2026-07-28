"""U4:make_real_compile_fix_loop_node build_template_text 注入单测(强化 fix_loop A1)。

覆盖:
- build_template_text 非空 → fix_node prompt 含模板原文 + 禁令(ascendc_add_ops / npu_op_package / -j*) case
- build_template_text=None 降级:不抛错,prompt 不含【构建参考】段
- build_template_text 形参透传到 fix_node 构造不抛
"""

from __future__ import annotations

from ascend_op_agent.orchestrator.nodes.validation import (
    make_real_compile_fix_loop_node,
)


# ---- mocks ----


class _FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class _CapturingAgent:
    """run_conversation 捕获 user_input(即 task_prompt)。"""

    def __init__(self) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = _FakeMemory()
        self._tool_calls_log: list[dict] = []
        self.captured_prompt: str | None = None

    def run_conversation(
        self,
        user_input,
        skills_layer_override=None,
        *,
        task_type=None,
        no_tools=False,
    ) -> str:
        self.captured_prompt = user_input
        self._conversation_history.append({"role": "user", "content": user_input})
        # 也 append assistant 让 markdown fallback 不参与(本测试不关心 files)
        self._conversation_history.append({"role": "assistant", "content": "ok"})
        return "ok"


class _FakeExecutor:
    def __init__(self, results: list[dict]) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    def compile_to_dict(self, operator_path: str) -> dict:
        self.calls.append(operator_path)
        return self._results.pop(0)


def _base_state() -> dict:
    return {"code_result": None, "messages": [], "memory_pools": {}}


def _resolver(path: str):
    return lambda _state: path


def _fail_compile_result(path: str = "/tmp/op") -> dict:
    return {
        "success": False,
        "return_code": 1,
        "stdout": "",
        "stderr": "build error",
        "command": "build.sh",
        "operator_path": path,
        "soc_version": "ascend910b",
    }


def _ok_compile_result(path: str = "/tmp/op") -> dict:
    return {
        "success": True,
        "return_code": 0,
        "stdout": "ok",
        "stderr": "",
        "command": "build.sh",
        "operator_path": path,
        "soc_version": "ascend910b",
    }


# ---- 核心:build_template_text 非空 → prompt 含模板 + 禁令 ----


def test_prompt_includes_build_template_and_ban_when_provided():
    """build_template_text 非空时,fix_node prompt 内联其内容 + 显式禁令段。"""
    captured: list[_CapturingAgent] = []

    def factory() -> _CapturingAgent:
        a = _CapturingAgent()
        captured.append(a)
        return a

    sentinel = "SAMPLE BUILD TEMPLATE npu_op_package WITH -j*) ADD_EXAMPLE_MARKER"
    node = make_real_compile_fix_loop_node(
        executor=_FakeExecutor([_fail_compile_result(), _ok_compile_result()]),
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=factory,
        sync_fn=None,
        build_template_text=sentinel,
        max_rounds=3,
    )
    node.func(_base_state())
    assert captured, "fix_node 应被调一次"
    prompt = captured[0].captured_prompt
    assert prompt is not None
    # build_template_text 内容注入
    assert sentinel in prompt, "build_template_text 应原样内联到 prompt"
    # 显式禁令
    assert "ascendc_add_ops" in prompt, "禁令段应提 ascendc_add_ops"
    assert "npu_op_package(" in prompt, "禁令段应提 npu_op_package() 宏"
    assert "-j*)" in prompt, "禁令段应提 -j*) case"
    assert "--soc=*)" in prompt, "禁令段应提 --soc=*) case"
    # 基底 prompt 还在(向后兼容)
    assert "Ascend C 编译错误修复专家" in prompt


# ---- build_template_text=None 降级(回归) ----


def test_prompt_degrades_without_build_template():
    """build_template_text=None 时,fix_node prompt 不含【构建参考】/【禁令】段,不抛错。"""
    captured: list[_CapturingAgent] = []

    def factory() -> _CapturingAgent:
        a = _CapturingAgent()
        captured.append(a)
        return a

    node = make_real_compile_fix_loop_node(
        executor=_FakeExecutor([_fail_compile_result(), _ok_compile_result()]),
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=factory,
        sync_fn=None,
        build_template_text=None,
        max_rounds=3,
    )
    node.func(_base_state())
    assert captured, "fix_node 应被调一次"
    prompt = captured[0].captured_prompt
    assert prompt is not None
    # 降级:不含【构建参考】/【禁令】段
    assert "【构建参考" not in prompt
    assert "【禁令 —— 必须遵守】" not in prompt
    # 但基底 prompt 保留(向后兼容)
    assert "Ascend C 编译错误修复专家" in prompt


# ---- 形参透传到 fix_node 构造不抛 ----


def test_build_template_text_param_accepted_at_construction():
    """build_template_text 形参可接任意字符串(空/非空/含特殊字符),构造不抛。"""
    for v in ["", " ", "模板内容 with npu_op_package & -j*)", "# cmake\nnpu_op_package(x)"]:
        node = make_real_compile_fix_loop_node(
            executor=_FakeExecutor([_ok_compile_result()]),
            operator_path_resolver=_resolver("/tmp/op"),
            agent_factory=lambda: _CapturingAgent(),
            sync_fn=None,
            build_template_text=v,
        )
        assert node is not None
