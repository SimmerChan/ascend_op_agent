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

"""Skill仓库管理模块

参考Hermes Agent实现，支持:
- Skill仓库动态发现
- 两层缓存机制（LRU + 磁盘快照）
- SQLite FTS5全文搜索
- 交互式skill选择
"""

from ascend_op_agent.skills.repository import SkillRepository, SkillRepositoryDiscovery
from ascend_op_agent.skills.index import SkillIndex
from ascend_op_agent.skills.storage import SkillStorage
from ascend_op_agent.skills.interactive import InteractiveSelector, format_skills_for_selection
from ascend_op_agent.skills.models import Skill, SkillInfo

__all__ = [
    "SkillRepository",
    "SkillRepositoryDiscovery",
    "SkillIndex",
    "SkillStorage",
    "InteractiveSelector",
    "format_skills_for_selection",
    "Skill",
    "SkillInfo",
]
