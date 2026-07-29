// U8: agent.progress 通知的 discriminated parser
//
// 后端(U6 _phase_callback)发 agent.progress 时,Python 端发:
//   {"phase": "...", "event": "started"|"completed"|"interrupted"|"failed", "payload": {...}}
//
// skill.usage 事件复用 agent.progress(不引入新方法),用 discriminator 字段区分:
//   - payload.event == "skill.usage" → SkillLoad state
//   - 其他 event                    → ProgressBar state(原有)
//
// U6 之前 frontend 只识别 stage/tool_name 字段,U8 加 discriminator 路由。

export type ProgressKind =
  | {
      kind: 'phase';
      stage?: 'thinking' | 'tool_executing' | 'completed' | 'waiting';
      tool_name?: string;
      error_code?: string;
      error_message?: string;
      event?: string;
      delta?: string;
    }
  | {
      kind: 'skill_usage';
      phase: string;
      thread_id?: string;
      skill_names: string[];
      used_skills: string[];
    };

const KNOWN_STAGES = new Set([
  'thinking',
  'tool_executing',
  'completed',
  'waiting',
]);

function asString(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === 'string');
}

export function parseProgressNotification(
  params: Record<string, unknown> | undefined
): ProgressKind {
  if (!params) return { kind: 'phase', stage: 'thinking' };

  const payload = (params.payload as Record<string, unknown> | undefined) ?? {};
  const event = asString(payload.event) ?? asString(params.event) ?? '';

  // skill.usage discriminator —— 复用 agent.progress 方法名,U2/U5 的 SkillUsageRegistry
  if (event === 'skill_usage') {
    const skillNames = asStringArray(payload.skill_names);
    const usedSkills = asStringArray(payload.used_skills);
    return {
      kind: 'skill_usage',
      phase: asString(payload.phase) ?? asString(params.phase) ?? 'unknown',
      thread_id: asString(payload.thread_id),
      skill_names: skillNames,
      used_skills: usedSkills,
    };
  }

  // phase 路由(原有 ProgressBar)
  const stageRaw = asString(payload.stage) ?? asString(params.stage);
  const stage = KNOWN_STAGES.has(stageRaw ?? '')
    ? (stageRaw as 'thinking' | 'tool_executing' | 'completed' | 'waiting')
    : 'thinking';
  return {
    kind: 'phase',
    stage,
    tool_name: asString(payload.tool_name) ?? asString(params.tool_name),
    error_code: asString(payload.error_code) ?? asString(params.error_code),
    error_message:
      asString(payload.error_message) ?? asString(params.error_message),
    event,
    delta: asString(payload.delta) ?? asString(params.delta),
  };
}
