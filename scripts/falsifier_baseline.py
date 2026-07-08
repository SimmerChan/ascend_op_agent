#!/usr/bin/env python3
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

"""一期-a falsifier baseline 度量脚本(R16 go/no-go gate)。

报告 task_store 当前状态(任务/线程/active)作为 dogfood 前 baseline。dogfood
2 周期间手动记录两个 metric,对比 baseline 决定 一期-a → 一期-b go/no-go:

  1. 用户自发切任务次数(set_active 调用,反映多任务并行真实发生)
  2. 手动 context-juggling 投诉数(用户报告上下文混乱/进展不可查)

gate(R16):metric 未达阈值 → abandon task 层(R5 fallback 只管 router,
本 gate 是 task 层 kill-switch)。

用法:
  PYTHONPATH=src python scripts/falsifier_baseline.py [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import sys

from ascend_op_agent.task_store import TaskStore


def build_report(store: TaskStore) -> dict:
    tasks = store.list_tasks()
    return {
        "task_count": len(tasks),
        "active_task_id": store.get_active(),
        "tasks": [
            {
                "id": t.id,
                "type": t.type,
                "created_at": t.created_at,
                "state_native": t.state_native,
                "thread_count": len(store.get_task_threads(t.id)),
            }
            for t in tasks
        ],
        "dogfood_metrics_manual": {
            "spontaneous_task_switches": "手动记录:set_active 调用次数(多任务并行真实发生)",
            "context_juggling_complaints": "手动记录:用户报告上下文混乱/进展不可查次数",
        },
        "gate_rule": (
            "R16:dogfood 2 周后,若自发切任务 < 阈值 且 无 context-juggling 投诉 "
            "→ task 层痛点未验证 → abandon(本 gate 是 task 层 kill-switch)"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="一期-a falsifier baseline(R16 gate)")
    ap.add_argument("--db", default=TaskStore.DEFAULT_DB_PATH, help="tasks.db 路径")
    args = ap.parse_args()
    store = TaskStore(args.db)
    report = build_report(store)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
