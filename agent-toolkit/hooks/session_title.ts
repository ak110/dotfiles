import type { On } from "claude-code";

type TitleState = {
  session_title_kind?: string;
  current_plan_file_path?: string;
  last_hook_session_title?: string;
};

const SKILL_KINDS: Record<string, string> = {
  "process-wi": "process-wi",
  "single-lane-process": "single-lane",
  "add-awi-by-user": "add-awi",
};

function stateCommand(root: string, sessionId: string, extra: string[] = []): string[] {
  return ["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python", `${root}/scripts/launch.py`,
    "agent_toolkit/_common/session_state.py", "--session-id", sessionId, ...extra];
}

// 計画のパスはPostToolUseが計画メインファイルとして検証してから記録する。
function proposedTitle(state: TitleState, loop: boolean): string | undefined {
  const kind = loop ? "process-wi" : state.session_title_kind;
  const basename = typeof state.current_plan_file_path === "string" ? state.current_plan_file_path.split(/[\\/]/).at(-1) : undefined;
  const plan = basename?.replace(/\.md$/, "").replace(/^\d{2}-\d{4}_/, "");
  return [kind, plan].filter(Boolean).join(" ") || undefined;
}

export function register(on: On): void {
  on("skill.prompt", { skill: /^(agent-toolkit:)?(process-wi|single-lane-process|add-awi-by-user)$/ }, async ($, e, next) => {
    const result = await next(e);
    const kind = SKILL_KINDS[e.skill.replace(/^agent-toolkit:/, "")];
    if (kind) {
      const saved = await $.process.run(stateCommand($.plugin.root, await $.session.id(), [`--kind=${kind}`]), { timeoutMs: 30_000 });
      if (saved.exitCode !== 0) $.ui.log(`セッション種別を保存できない: ${saved.stderr}`, { to: "debug" });
    }
    return result;
  }).catch(($, e, next) => next(e));

  on("classic.UserPromptSubmit", async ($, e, next) => {
    const result = await next(e);
    const sessionId = e.session_id;
    const loaded = await $.process.run(stateCommand($.plugin.root, sessionId), { timeoutMs: 30_000 });
    if (loaded.exitCode !== 0 || loaded.isStdoutTruncated) return result;
    const state: TitleState = JSON.parse(loaded.stdout);
    const expectedId = await $.env.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID");
    const loop = (await $.env.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION")) === "1" &&
      (expectedId === undefined || Boolean(expectedId) && expectedId === sessionId);
    const title = proposedTitle(state, loop);
    const current = "session_title" in e ? e.session_title : undefined;
    if (!title || title === current || current && current !== state.last_hook_session_title) return result;
    const saved = await $.process.run(stateCommand($.plugin.root, sessionId, [`--title=${title}`]), { timeoutMs: 30_000 });
    return saved.exitCode === 0 ? { ...result, sessionTitle: title } : result;
  }).catch(($, e, next) => {
    $.ui.log(`セッション名を設定できない: ${String(next.error)}`, { to: "debug" });
    return next(e);
  });
}
