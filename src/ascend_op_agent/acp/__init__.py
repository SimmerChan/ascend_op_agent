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

"""ACP (Agent Client Protocol) 适配器模块

使 Ascend Op Agent 可以作为 VS Code、Zed、JetBrains 等编辑器的 AI 后端运行。
通过 stdio 进行 JSON-RPC 2.0 通信。
"""

from .adapter import ACPAdapter
from .session import ACPSession, SessionManager

__all__ = ["ACPAdapter", "ACPSession", "SessionManager"]
