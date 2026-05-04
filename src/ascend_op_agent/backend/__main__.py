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

"""Backend CLI 入口点

通过 -m ascend_op_agent.backend 调用
"""

import sys
import importlib.util
from pathlib import Path

# 路径: src/ascend_op_agent/backend/__main__.py
# backend.py 在 src/ascend_op_agent/backend.py (不是 backend/ 子目录)
_current_file = Path(__file__).expanduser().resolve()
_pkg_dir = _current_file.parent  # backend/
_project_root = _pkg_dir.parent  # ascend_op_agent/
_backend_py = _project_root / "backend.py"  # ascend_op_agent/backend.py

if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 直接加载 backend.py 模块
spec = importlib.util.spec_from_file_location("ascend_op_agent_backend_module", _backend_py)
backend_module = importlib.util.module_from_spec(spec)
sys.modules["ascend_op_agent_backend_module"] = backend_module
spec.loader.exec_module(backend_module)

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(backend_module.main())
    except KeyboardInterrupt:
        pass