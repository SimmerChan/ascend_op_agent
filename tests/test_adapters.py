# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Framework Adapters模块测试"""

import pytest

from ascend_op_agent.workflow import OpInfo, DesignDoc
from ascend_op_agent.workflow.adapters import (
    PyTorchAdapter,
    TensorFlowAdapter,
    AdapterResult,
    generate_framework_adapter,
)


class TestPyTorchAdapter:
    """PyTorchAdapter测试"""

    def test_generation(self):
        """测试生成"""
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        design_doc = DesignDoc(op_info=op_info)

        adapter = PyTorchAdapter(op_info, design_doc)
        result = adapter.generate()

        assert result.success is True
        assert result.framework == "pytorch"
        assert len(result.generated_files) == 3
        assert "torch_test_op_op.cpp" in result.generated_files
        assert "torch_test_op_wrapper.py" in result.generated_files
        assert "test_torch_test_op.py" in result.generated_files

    def test_generated_files_content(self):
        """测试生成的文件内容"""
        op_info = OpInfo(
            name="my_op",
            description="My operator",
            op_type="elementwise",
            input_shapes=[[10, 10]],
            input_dtypes=["float32"],
            output_shapes=[[10, 10]],
        )
        design_doc = DesignDoc(op_info=op_info)

        adapter = PyTorchAdapter(op_info, design_doc)
        result = adapter.generate()

        assert result.success is True

        # 检查C++文件
        cpp_file = next(f for f in result.files if "my_op_op.cpp" in f.path)
        assert "my_op_forward" in cpp_file.content
        assert "TORCH_LIBRARY" in cpp_file.content

        # 检查Python文件
        py_file = next(f for f in result.files if "my_op_wrapper.py" in f.path)
        assert "class MyOpOp" in py_file.content
        assert "torch.autograd.Function" in py_file.content


class TestTensorFlowAdapter:
    """TensorFlowAdapter测试"""

    def test_generation(self):
        """测试生成"""
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        design_doc = DesignDoc(op_info=op_info)

        adapter = TensorFlowAdapter(op_info, design_doc)
        result = adapter.generate()

        assert result.success is True
        assert result.framework == "tensorflow"
        assert len(result.generated_files) == 3
        assert "tf_test_op_op.cc" in result.generated_files
        assert "tf_test_op_wrapper.py" in result.generated_files
        assert "test_tf_test_op.py" in result.generated_files

    def test_generated_files_content(self):
        """测试生成的文件内容"""
        op_info = OpInfo(
            name="my_op",
            description="My operator",
            op_type="elementwise",
            input_shapes=[[10, 10]],
            input_dtypes=["float32"],
            output_shapes=[[10, 10]],
        )
        design_doc = DesignDoc(op_info=op_info)

        adapter = TensorFlowAdapter(op_info, design_doc)
        result = adapter.generate()

        assert result.success is True

        # 检查C++文件
        cc_file = next(f for f in result.files if "my_op_op.cc" in f.path)
        assert "tensorflow" in cc_file.content
        assert "OpKernel" in cc_file.content

        # 检查Python文件
        py_file = next(f for f in result.files if "my_op_wrapper.py" in f.path)
        assert "class MyOpLayer" in py_file.content
        assert "tf.keras.layers.Layer" in py_file.content


class TestAdapterResult:
    """AdapterResult测试"""

    def test_creation(self):
        """测试创建"""
        result = AdapterResult(
            success=True,
            files=[],
            generated_files=["test.cpp"],
            framework="pytorch",
        )

        assert result.success is True
        assert result.framework == "pytorch"

    def test_error_handling(self):
        """测试错误处理"""
        result = AdapterResult(
            success=False,
            errors=["Some error"],
            framework="tensorflow",
        )

        assert result.success is False
        assert len(result.errors) == 1


class TestGenerateFrameworkAdapter:
    """generate_framework_adapter工厂函数测试"""

    def test_pytorch_adapter(self):
        """测试PyTorch适配器生成"""
        op_info = OpInfo(
            name="test_op",
            description="Test",
            op_type="elementwise",
        )
        design_doc = DesignDoc(op_info=op_info)

        result = generate_framework_adapter(op_info, design_doc, "pytorch")

        assert result.success is True
        assert result.framework == "pytorch"

    def test_tensorflow_adapter(self):
        """测试TensorFlow适配器生成"""
        op_info = OpInfo(
            name="test_op",
            description="Test",
            op_type="elementwise",
        )
        design_doc = DesignDoc(op_info=op_info)

        result = generate_framework_adapter(op_info, design_doc, "tensorflow")

        assert result.success is True
        assert result.framework == "tensorflow"

    def test_unsupported_framework(self):
        """测试不支持的框架"""
        op_info = OpInfo(
            name="test_op",
            description="Test",
            op_type="elementwise",
        )
        design_doc = DesignDoc(op_info=op_info)

        result = generate_framework_adapter(op_info, design_doc, "mxnet")

        assert result.success is False
        assert "Unsupported framework" in result.errors[0]
