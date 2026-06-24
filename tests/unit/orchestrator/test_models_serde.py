"""U5 dataclass serde 单测。

覆盖 6 个核心类(OpInfo/DesignDoc/CodeGenResult/CompileResult/PrecisionReport/PhaseResult)
+ 4 个辅助递归类(ArchitectureMapping/FileChange/TestCase/TestResult)的 to_dict/from_dict。

关键断言:

- ``X.from_dict(x.to_dict()) == x`` round-trip
- Enum 字段(migration_strategy / status)round-trip 后类型保持
- CodeGenResult.to_dict 现含 files[*].content(修复旧丢 content bug)
- DesignDoc 递归 op_info + arch_mapping
- PrecisionReport 递归 test_results[*].test_case
- ArchitectureMapping 类变量(CUDA_MAPPINGS 等)不进 dict
- Enum 强制:无效值抛 ValueError
- 与 CheckpointStore 集成:嵌入 OpState 作 dict 后 JSON 序列化成功
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields

import pytest

from ascend_op_agent.orchestrator.checkpoint import CheckpointStore
from ascend_op_agent.workflow.models import (
    ArchitectureMapping,
    CodeGenResult,
    CompileResult,
    DesignDoc,
    FileChange,
    MigrationStrategy,
    OpInfo,
    PhaseResult,
    PhaseStatus,
    PrecisionReport,
    TestCase,
    TestResult,
)


def _round_trip(obj):
    return type(obj).from_dict(obj.to_dict())


def test_op_info_round_trip_preserves_enum() -> None:
    op = OpInfo(
        name="add",
        description="element-wise add",
        op_type="elementwise",
        input_shapes=[[2, 3], [2, 3]],
        input_dtypes=["float16", "float16"],
        output_shapes=[[2, 3]],
        output_dtypes=["float16"],
        migration_strategy=MigrationStrategy.CUDA_TO_ASCENDC,
        ref_code_path="vendor/cuda/add.cu",
        ref_code_type="cuda",
        attributes={"alpha": 1.0},
    )
    rt = _round_trip(op)
    assert rt == op
    assert rt.migration_strategy == MigrationStrategy.CUDA_TO_ASCENDC
    assert isinstance(rt.migration_strategy, MigrationStrategy)


def test_op_info_default_strategy_round_trip() -> None:
    op = OpInfo(name="x", description="d", op_type="elementwise")
    rt = _round_trip(op)
    assert rt == op
    assert rt.migration_strategy == MigrationStrategy.FROM_SCRATCH


def test_op_info_invalid_enum_raises() -> None:
    with pytest.raises(ValueError):
        OpInfo.from_dict(
            {
                "name": "x",
                "description": "d",
                "op_type": "e",
                "migration_strategy": "not_a_real_strategy",
            }
        )


def test_architecture_mapping_excludes_class_vars() -> None:
    am = ArchitectureMapping.get_mapping("cuda")
    d = am.to_dict()
    assert "source_type" in d
    assert "mappings" in d
    # 类变量不应进 dict
    assert "CUDA_MAPPINGS" not in d
    assert "TRITON_MAPPINGS" not in d
    rt = _round_trip(am)
    assert rt == am


def test_architecture_mapping_empty_round_trip() -> None:
    am = ArchitectureMapping(source_type="unknown", mappings={})
    rt = _round_trip(am)
    assert rt == am


def test_design_doc_recursive_round_trip() -> None:
    op = OpInfo(
        name="matmul",
        description="matrix mul",
        op_type="matmul",
        migration_strategy=MigrationStrategy.CUDA_TO_ASCENDC,
        ref_code_type="cuda",
    )
    am = ArchitectureMapping.get_mapping("cuda")
    doc = DesignDoc(
        op_info=op,
        input_layouts=["NCHW"],
        output_layouts=["NCHW"],
        tile_shape=[16, 16],
        block_dim=[8, 1, 1],
        arch_mapping=am,
        decisions=["use cube vector"],
        confirmed=True,
    )
    rt = _round_trip(doc)
    assert rt == doc
    assert isinstance(rt.op_info, OpInfo)
    assert isinstance(rt.arch_mapping, ArchitectureMapping)
    assert rt.op_info.migration_strategy == MigrationStrategy.CUDA_TO_ASCENDC


def test_design_doc_optional_fields_none_round_trip() -> None:
    op = OpInfo(name="x", description="d", op_type="e")
    doc = DesignDoc(op_info=op)
    rt = _round_trip(doc)
    assert rt == doc
    assert rt.arch_mapping is None
    assert rt.tile_shape is None


def test_file_change_round_trip() -> None:
    fc = FileChange(path="kernel.c", action="create", content="int main(){return 0;}")
    rt = _round_trip(fc)
    assert rt == fc


def test_code_gen_result_to_dict_includes_content() -> None:
    """关键:修复旧 to_dict 丢 content 的 bug。"""
    fc1 = FileChange(path="a.c", action="create", content="AAA")
    fc2 = FileChange(path="b.c", action="modify", content="BBB")
    cgr = CodeGenResult(
        success=True,
        files=[fc1, fc2],
        errors=[],
        warnings=["w1"],
        generated_files=["a.c", "b.c"],
    )
    d = cgr.to_dict()
    # 显式断言 content 在 dict 里
    assert d["files"][0]["content"] == "AAA"
    assert d["files"][1]["content"] == "BBB"
    assert d["files"][0]["action"] == "create"


def test_code_gen_result_round_trip() -> None:
    cgr = CodeGenResult(
        success=False,
        files=[FileChange(path="a.c", action="create", content="X")],
        errors=["syntax error"],
        warnings=[],
        generated_files=["a.c"],
    )
    rt = _round_trip(cgr)
    assert rt == cgr
    assert rt.files[0].content == "X"


def test_compile_result_round_trip() -> None:
    cr = CompileResult(
        success=False,
        command="make",
        stdout="ok",
        stderr="error L",
        return_code=2,
        fix_attempts=1,
        fixed=False,
        syntax_errors=["missing ;"],
        missing_headers=["<stdio.h>"],
        type_errors=["int->ptr"],
        other_errors=["unknown"],
    )
    rt = _round_trip(cr)
    assert rt == cr


def test_compile_result_minimal_round_trip() -> None:
    cr = CompileResult(success=True, command="c")
    rt = _round_trip(cr)
    assert rt == cr


def test_test_case_round_trip() -> None:
    tc = TestCase(
        name="case_1",
        input_shapes=[[2, 2], [2, 2]],
        input_dtypes=["float16", "float16"],
        expected_shapes=[[2, 2]],
        expected_dtypes=["float16"],
        attrs={"alpha": 0.5},
    )
    rt = _round_trip(tc)
    assert rt == tc


def test_test_result_round_trip() -> None:
    tc = TestCase(
        name="c",
        input_shapes=[[1]],
        input_dtypes=["float32"],
        expected_shapes=[[1]],
        expected_dtypes=["float32"],
    )
    tr = TestResult(
        test_case=tc,
        passed=True,
        abs_err=1e-6,
        rel_err=1e-7,
        cos_sim=0.99999,
        error_message=None,
    )
    rt = _round_trip(tr)
    assert rt == tr


def test_precision_report_recursive_round_trip() -> None:
    tc1 = TestCase(
        name="c1",
        input_shapes=[[2]],
        input_dtypes=["float16"],
        expected_shapes=[[2]],
        expected_dtypes=["float16"],
    )
    tc2 = TestCase(
        name="c2",
        input_shapes=[[3]],
        input_dtypes=["float16"],
        expected_shapes=[[3]],
        expected_dtypes=["float16"],
        attrs={"k": 1},
    )
    pr = PrecisionReport(
        operator_name="add",
        total_cases=2,
        passed_cases=1,
        failed_cases=1,
        test_results=[
            TestResult(test_case=tc1, passed=True, abs_err=0.0001, cos_sim=0.9999),
            TestResult(test_case=tc2, passed=False, error_message="mismatch"),
        ],
        avg_abs_err=0.0001,
        avg_rel_err=0.00001,
        avg_cos_sim=0.9999,
        report_path="test/prec.md",
        meets_requirement=False,
    )
    rt = _round_trip(pr)
    assert rt == pr
    assert len(rt.test_results) == 2
    assert isinstance(rt.test_results[0].test_case, TestCase)
    assert rt.test_results[1].test_case.attrs == {"k": 1}


def test_precision_report_empty_results_round_trip() -> None:
    pr = PrecisionReport(
        operator_name="x",
        total_cases=0,
        passed_cases=0,
        failed_cases=0,
    )
    rt = _round_trip(pr)
    assert rt == pr


def test_phase_result_round_trip_preserves_enum() -> None:
    pr = PhaseResult(
        phase_name="design",
        status=PhaseStatus.WAITING_CONFIRMATION,
        data={"design_doc": {"name": "Add"}},
        message="awaiting approval",
        errors=[],
    )
    rt = _round_trip(pr)
    assert rt == pr
    assert rt.status == PhaseStatus.WAITING_CONFIRMATION
    assert isinstance(rt.status, PhaseStatus)


def test_phase_result_invalid_status_raises() -> None:
    with pytest.raises(ValueError):
        PhaseResult.from_dict(
            {
                "phase_name": "x",
                "status": "not_a_status",
            }
        )


def test_phase_result_with_any_data_round_trip() -> None:
    pr = PhaseResult(
        phase_name="codegen",
        status=PhaseStatus.COMPLETED,
        data=None,
        message="",
        errors=["e1", "e2"],
    )
    rt = _round_trip(pr)
    assert rt == pr


def test_all_six_core_classes_json_serializable_via_dict() -> None:
    """嵌入 OpState 作 dict 后 JSON 序列化成功(支撑 checkpoint)。"""
    op = OpInfo(name="x", description="d", op_type="e")
    doc = DesignDoc(op_info=op, confirmed=True)
    cgr = CodeGenResult(success=True, files=[FileChange("a", "create", "X")])
    cr = CompileResult(success=True, command="make")
    pr = PrecisionReport(operator_name="x", total_cases=0, passed_cases=0, failed_cases=0)
    ph = PhaseResult(phase_name="p", status=PhaseStatus.COMPLETED)

    opstate_fragment = {
        "op_info": op.to_dict(),
        "design_doc": doc.to_dict(),
        "code_result": cgr.to_dict(),
        "compile_result": cr.to_dict(),
        "precision_report": pr.to_dict(),
        "last_phase_result": ph.to_dict(),
    }
    s = json.dumps(opstate_fragment, ensure_ascii=False)
    parsed = json.loads(s)
    assert OpInfo.from_dict(parsed["op_info"]) == op
    assert DesignDoc.from_dict(parsed["design_doc"]) == doc
    assert CodeGenResult.from_dict(parsed["code_result"]) == cgr
    assert CompileResult.from_dict(parsed["compile_result"]) == cr
    assert PrecisionReport.from_dict(parsed["precision_report"]) == pr
    assert PhaseResult.from_dict(parsed["last_phase_result"]) == ph


def test_dataclass_persists_through_checkpoint_store(tmp_path) -> None:
    """U5 的最终目的:dataclass 嵌入 OpState 作 dict,经 CheckpointStore round-trip。"""
    store = CheckpointStore(tmp_path / "ck.db")

    op = OpInfo(
        name="add",
        description="d",
        op_type="e",
        migration_strategy=MigrationStrategy.TRITON_TO_ASCENDC,
    )
    state = {
        "thread_id": "t1",
        "op_info": op.to_dict(),
        "design_doc": None,
        "messages": [],
    }
    store.save("t1", state, current_phase="analyze", status="running")

    loaded = store.load("t1")
    assert loaded["op_info"]["migration_strategy"] == "triton_to_ascendc"
    restored = OpInfo.from_dict(loaded["op_info"])
    assert restored == op
    assert restored.migration_strategy == MigrationStrategy.TRITON_TO_ASCENDC


def test_dataclass_field_count_unchanged() -> None:
    """回归:serde 添加不应意外增删字段。"""
    assert len(dataclass_fields(OpInfo)) == 11
    assert len(dataclass_fields(DesignDoc)) == 8
    assert len(dataclass_fields(CodeGenResult)) == 5
    assert len(dataclass_fields(CompileResult)) == 11
    assert len(dataclass_fields(PrecisionReport)) == 10
    assert len(dataclass_fields(PhaseResult)) == 5
