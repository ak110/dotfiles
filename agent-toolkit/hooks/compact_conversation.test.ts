import type { On } from "claude-code";
import { type Engine, expect, mock, test } from "claude-code/testing";
import { register } from "./register.ts";

function registrationViolations(event: unknown, matcher: unknown): string[] {
  const matches = (matcher: unknown, value: string): boolean => {
    if (matcher === undefined) return true;
    if (typeof matcher === "string") return matcher === value;
    if (matcher instanceof RegExp) { matcher.lastIndex = 0; return matcher.test(value); }
    throw new Error(`未対応のmatcher: ${String(matcher)}`);
  };
  const protectedEvents = ["prompt.section", "prompt.context", "prompt.attachment", "prompt.compose", "skill.prompt", "attribution.text", "session.compact", "settings.read"];
  const violations = protectedEvents.filter(name => matches(event, name));
  if (typeof event === "string" && event.startsWith("classic.")) violations.push(event);
  if (matches(event, "session.append")) {
    const door = typeof matcher === "object" && matcher !== null ? (matcher as { door?: unknown }).door : undefined;
    violations.push(...["attachment", "hook-context", "compaction"].filter(name => matches(door, name)));
  }
  return violations;
}

test("全機能の実登録matcherは組織ガードの保護対象を購読しない", () => {
  let count = 0;
  const capture = ((event: unknown, matcher: unknown) => {
    count++;
    expect(registrationViolations(event, matcher)).toEqual([]);
    return { catch: () => undefined };
  }) as unknown as On;
  register(capture, {});
  expect(count > 0).toBe(true);
});

test("他機能への保護door・無条件・広域イベント登録の持込みを検出する", () => {
  for (const matcher of [{ door: "attachment" }, { door: /^(delivery|queue|attachment)$/ }, { door: /hook-context/ }, { door: /compaction/g }, {}]) {
    expect(registrationViolations("session.append", matcher).length > 0).toBe(true);
  }
  expect(registrationViolations("session.append", () => undefined).length > 0).toBe(true);
  expect(registrationViolations(/.*/, {}).length > 0).toBe(true);
  expect(registrationViolations("session.append", { door: /^(delivery|queue)$/ })).toEqual([]);
});

const TOOL = "mcp__agent-toolkit__compact_conversation";

async function waitingEngine($: Engine, on: On, options: { percent?: number; eligible?: boolean; delegated?: boolean; compact?: "skip" | "failure" } = {}) {
  mock.env(on, options.delegated ? { AGENT_TOOLKIT_DELEGATED_SESSION: "1" } : {});
  mock.session(on);
  const clock = mock.clock(on, { now: 10_000 });
  const calls = { queries: [] as (readonly string[])[], compactions: 0, notices: [] as string[] };
  const usage = { percent: options.percent ?? 60 };
  const task = { id: "wait-task" };
  on("session.id", () => ({ value: "wait-main" }));
  on("session.start", (_$, e) => ({ cwd: e.cwd }));
  on("session.messages", () => ({ value: [] }));
  on("session.usage", () => ({ value: { startedAt: 0, context: { tokens: usage.percent, window: 100, percent: usage.percent }, rateLimits: [] } }));
  on("tool.register", (_$, e) => ({ value: { tool: e.name } }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("turn.complete", () => ({ text: "" }));
  on("tool.call", { tool: "Bash" }, () => ({ result: { backgroundTaskId: task.id } }));
  on("process.run", (_$, e) => {
    calls.queries.push(e.argv);
    return { value: { exitCode: 0, stdout: JSON.stringify({ eligible: options.eligible ?? true }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
  });
  on("session.compact", () => {
    calls.compactions++;
    if (options.compact === "failure") throw new Error("圧縮失敗");
    if (options.compact === "skip") return { skip: "ホストが見送り" };
    return { messages: [{ role: "user", text: "保存した作業状態", toolUses: [] }] };
  });
  on("ui.toast", (_$, e) => { calls.notices.push(e.text); return { value: undefined }; });
  await $.session.start({ cwd: "/work", surface: null, isInteractive: true });
  return { clock, calls, usage, task };
}

const MAIN_TURN = { answer: "", durationMs: 0, isAborted: false, turnId: "waiting-turn", reason: "answer" as const };

for (const mode of ["long", "low", "ineligible-wait-owner", "running", "delegated", "subagent", "skip", "failure"] as const) {
  test(`背景waitの自動圧縮条件: ${mode}`, async ($, on) => {
    const options = { percent: mode === "low" ? 49 : mode === "long" ? 50 : 60, eligible: mode !== "ineligible-wait-owner", delegated: mode === "delegated", ...(mode === "skip" || mode === "failure" ? { compact: mode } : {}) };
    const { clock, calls } = await waitingEngine($, on, options);
    if (mode === "running") await $.turn.start({ text: "待機を開始", turnId: MAIN_TURN.turnId });
    await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true, ...(mode === "subagent" ? { agentId: "child" } : {}) });
    await clock.advance(4999);
    expect(calls.compactions).toBe(0);
    expect(calls.queries.length).toBe(0);
    await clock.advance(1);
    if (mode === "running") {
      expect(calls.queries.length).toBe(0);
      await $.turn.complete(MAIN_TURN);
    }
    await clock.advance(0);
    const eligible = ["long", "running", "skip", "failure"].includes(mode);
    expect(calls.compactions).toBe(eligible ? 1 : 0);
    expect(calls.queries.length).toBe(eligible || mode === "ineligible-wait-owner" ? 1 : 0);
    for (const query of calls.queries) {
      expect(query.slice(0, 7)).toEqual(["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python"]);
      expect(query[7]?.endsWith("/scripts/launch.py")).toBe(true);
      expect(query[8]).toBe(query[7]?.replace(/\/scripts\/launch\.py$/, "/agent_toolkit/atk.py"));
      expect(query.slice(9, 12)).toEqual(["run-script", "wait-compaction-context", "--"]);
      expect(query).not.toContain("--not-before");
      expect(query).toContain("--session-id");
      expect(query[query.indexOf("--session-id") + 1]).toBe("wait-main");
      expect(query).toContain("--command=atk agents wait");
    }
    await $.turn.complete(MAIN_TURN);
    await clock.advance(5000);
    expect(calls.compactions).toBe(eligible ? 1 : 0);
    expect(calls.notices.length).toBe(mode === "skip" || mode === "failure" ? 1 : 0);
  });
}

for (const door of ["delivery", "queue"] as const) {
  for (const status of ["completed", "failed", "killed"]) {
    test(`5秒前の${door}の${status}通知で同じ背景waitの自動圧縮を取り消す`, async ($, on) => {
      const { clock, calls } = await waitingEngine($, on);
      await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
      await $.session.append({ door, origin: { kind: "engine" }, uuid: "done", message: { type: "user", role: "user", content: [{ type: "text", text: `<task-notification><task-id>wait-task</task-id><status>${status}</status></task-notification>` }] } });
      await clock.advance(5000);
      expect(calls.queries.length).toBe(0);
      expect(calls.compactions).toBe(0);
    });
  }
}

test("高利用率の新しい待機でも反復せず、閾値未満を観測した後の新作業で再開する", async ($, on) => {
  const { clock, calls, usage, task } = await waitingEngine($, on);
  await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
  await clock.advance(5000);
  await clock.advance(0);
  expect(calls.compactions).toBe(1);
  task.id = "second-wait";
  await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
  await clock.advance(5000);
  await clock.advance(0);
  expect(calls.compactions).toBe(1);
  expect(calls.queries.length).toBe(1);
  usage.percent = 10;
  await $.turn.start({ text: "圧縮後の作業", turnId: MAIN_TURN.turnId });
  await $.turn.complete(MAIN_TURN);
  usage.percent = 70;
  await $.turn.start({ text: "新しい作業", turnId: MAIN_TURN.turnId });
  await $.turn.complete(MAIN_TURN);
  await clock.advance(0);
  expect(calls.compactions).toBe(2);
  expect(calls.queries.length).toBe(2);
});

test("実行中turnの予約はturn完了後の単発タイマーで圧縮する", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  let calls = 0;
  on("session.id", () => ({ value: "compact-test" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("turn.complete", () => ({ text: "" }));
  on("session.compact", () => {
    calls++;
    return { messages: [{ role: "user", text: "保存済みの作業状態", toolUses: [] }] };
  });
  await $.turn.start({ text: "作業", turnId: "t1" });
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toBe(0);
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" });
  await clock.advance(0);
  expect(calls).toBe(1);
});

test("skipを成功とせず通知し、次の明示予約を受け付ける", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  const notes: string[] = [];
  let calls = 0;
  on("session.compact", () => { calls++; return { skip: "状態保存待ち" }; });
  on("ui.toast", (_$, e) => { notes.push(e.text); return { value: undefined }; });
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(notes.length).toBe(1);
  expect(notes[0]).toMatch("見送られた");
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toBe(2);
});

test("受付と重複の応答後に一度だけ圧縮し、完了後は再予約できる", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  const calls: number[] = [];
  on("session.compact", () => {
    calls.push(1);
    return { messages: [{ role: "user", text: "圧縮後の引継ぎ", toolUses: [] }] };
  });
  expect((await $.tool.check({ tool: TOOL, input: {} })).decision).toBe("allow");
  const first = await $.tool.call({ tool: TOOL });
  expect(first.result).toMatch("まだ完了していない");
  const duplicate = await $.tool.call({ tool: TOOL });
  expect(duplicate.result).toMatch("追加していない");
  expect(calls).toEqual([]);
  await clock.advance(0);
  expect(calls).toEqual([1]);
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toEqual([1, 1]);
});

test("実行拒否は同じ会話へ通知し、失敗後に再予約できる", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  const prompts: string[] = [];
  let calls = 0;
  on("session.compact", () => {
    calls++;
    if (calls === 1) throw new Error("圧縮の実行拒否");
    return { messages: [{ role: "user", text: "圧縮後の引継ぎ", toolUses: [] }] };
  });
  on("ui.toast", (_$, e) => {
    prompts.push(e.text);
    return { value: undefined };
  });
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(prompts.length).toBe(1);
  expect(prompts[0]?.startsWith('<atk-auto source="compact-conversation" kind="warn">\n')).toBe(true);
  expect(prompts[0]?.endsWith("\n</atk-auto>")).toBe(true);
  // hookの例外はホストが次の実装へ渡す。実装が無い拒否が呼出元へ届く。
  expect(prompts[0]).toMatch("会話圧縮の予約実行に失敗した");
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toBe(2);
  expect(prompts.length).toBe(1);
});

test("サブエージェントからの呼び出しは予約しない", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  let calls = 0;
  on("session.compact", () => {
    calls++;
    return { messages: [] };
  });
  expect((await $.tool.call({ tool: TOOL, agentId: "child" })).deny).toMatch("メイン");
  await clock.advance(0);
  expect(calls).toBe(0);
});

// `delegated_session.py`の`is_delegated`が委譲先と判定する印の組合せ。委譲先への登録の除外は`delegated_session.test.ts`が検証する。
const DELEGATED_ENVS: Readonly<Record<string, string>>[] = [
  { AGENT_TOOLKIT_DELEGATED_SESSION: "1" },
  { AGENT_TOOLKIT_OWNER_SESSION: "owner" },
  { AGENT_TOOLKIT_DELEGATED_SESSION: "1", AGENT_TOOLKIT_OWNER_SESSION: "owner" },
];

for (const env of DELEGATED_ENVS) {
  test(`委譲先（${JSON.stringify(env)}）からは圧縮を許可も予約もしない`, async ($, on) => {
    mock.env(on, env);
    const clock = mock.clock(on);
    let calls = 0;
    on("session.compact", () => { calls++; return { messages: [] }; });
    expect((await $.tool.check({ tool: TOOL, input: {} })).decision).toBe("deny");
    expect((await $.tool.call({ tool: TOOL })).deny).toMatch("メイン");
    await clock.advance(0);
    expect(calls).toBe(0);
  });
}
