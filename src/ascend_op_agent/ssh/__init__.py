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

"""SSH 模块

支持本地和远程开发模式，提供 SSH 连接和文件同步功能。
"""

from ascend_op_agent.ssh.manager import SSHManager, SSHConnectionError
from ascend_op_agent.ssh.sync import FileSync, SyncDirection
from ascend_op_agent.ssh.env_config import RemoteEnvConfig, RemoteEnvValidator

__all__ = [
    "SSHManager",
    "SSHConnectionError",
    "FileSync",
    "SyncDirection",
    "RemoteEnvConfig",
    "RemoteEnvValidator",
]
