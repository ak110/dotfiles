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
  const protectedEvents = ["prompt.section", "prompt.context", "prompt.attachment", "prompt.compose", "skill.prompt", "attribution.text", "settings.read"];
  const violations = protectedEvents.filter(name => matches(event, name));
  if (typeof event === "string" && event.startsWith("classic.")) violations.push(event);
  if (matches(event, "session.append")) {
    const door = typeof matcher === "object" && matcher !== null ? (matcher as { door?: unknown }).door : undefined;
    violations.push(...["attachment", "hook-context", "compaction"].filter(name => matches(door, name)));
  }
  return violations;
}

test("圧縮置換以外の実登録matcherは組織ガードの保護対象を購読しない", () => {
  let count = 0;
  const capture = ((event: unknown, matcher: unknown) => {
    count++;
    expect(registrationViolations(event, matcher)).toEqual([]);
    return { catch: () => undefined };
  }) as unknown as On;
  register(capture, {});
  expect(count > 0).toBe(true);
});

for (const trigger of ["manual", "auto", "plugin", "precompute"] as const) {
  test(`main会話の${trigger}は原本を参照して要約を呼ばない`, async ($, on) => {
    mock.env(on, {});
    let summaries = 0;
    on("session.id", () => ({ value: "current-session" }));
    on("process.run", (_$, e) => {
      expect(e.argv.slice(-2)).toEqual(["--session-id", "current-session"]);
      return { value: { exitCode: 0, stdout: JSON.stringify({ record_path: "/records/current-session.jsonl" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("session.compact", () => { summaries++; return { messages: [] }; });
    const result = await $.session.compact({ trigger, messages: [] });
    expect(summaries).toBe(0);
    if (trigger === "precompute") {
      expect(result.skip).toMatch("先行生成");
    } else {
      expect(result.messages?.length).toBe(1);
      expect(result.messages?.[0]?.text).toMatch("/records/current-session.jsonl");
      expect(result.messages?.[0]?.text).toMatch("最新の訂正");
      expect("usage" in result).toBe(false);
    }
  });
}

for (const condition of ["subagent", "unresolved", "failure", "truncated"] as const) {
  test(`原本置換できない${condition}では標準圧縮を保つ`, async ($, on) => {
    mock.env(on, {});
    let summaries = 0;
    let resolutions = 0;
    on("session.id", () => ({ value: "current-session" }));
    on("process.run", () => {
      resolutions++;
      if (condition === "failure") throw new Error("記録を取得できない");
      return { value: { exitCode: condition === "unresolved" ? 2 : 0, stdout: JSON.stringify({ record_path: "/records/current-session.jsonl" }), stderr: "", isStdoutTruncated: condition === "truncated", isStderrTruncated: false } };
    });
    on("session.compact", () => { summaries++; return { messages: [{ role: "user", text: "標準の圧縮結果", toolUses: [] }] }; });
    const result = await $.session.compact({ trigger: "manual", messages: [], ...(condition === "subagent" ? { agentId: "child" } : {}) });
    expect(summaries).toBe(1);
    expect(resolutions).toBe(condition === "subagent" ? 0 : 1);
    expect(result.messages?.[0]?.text).toBe("標準の圧縮結果");
  });
}

test("他機能への保護door・無条件・広域イベント登録の持込みを検出する", () => {
  for (const matcher of [{ door: "attachment" }, { door: /^(delivery|queue|attachment)$/ }, { door: /hook-context/ }, { door: /compaction/g }, {}]) {
    expect(registrationViolations("session.append", matcher).length > 0).toBe(true);
  }
  expect(registrationViolations("session.append", () => undefined).length > 0).toBe(true);
  expect(registrationViolations(/.*/, {}).length > 0).toBe(true);
  expect(registrationViolations("session.append", { door: /^(delivery|queue)$/ })).toEqual([]);
});

const TOOL = "mcp__agent-toolkit__compact_conversation";

async function waitingEngine($: Engine, on: On, options: { percent?: number; window?: number; eligible?: boolean; delegated?: boolean; compact?: "skip" | "failure"; writeFails?: boolean; bashResult?: unknown } = {}) {
  mock.env(on, { HOME: "/home/tester", ...(options.delegated ? { AGENT_TOOLKIT_DELEGATED_SESSION: "1" } : {}) });
  mock.session(on);
  const clock = mock.clock(on, { now: 10_000 });
  const calls = { queries: [] as (readonly string[])[], compactions: 0, notices: [] as string[], diagnostics: [] as string[], records: new Map<string, string>() };
  on("fs.write", (_$, e) => {
    if (options.writeFails) throw new Error("書込不可");
    calls.records.set(e.path, e.text);
    return { value: undefined };
  });
  const usage = { percent: options.percent ?? 60 };
  const task = { id: "wait-task" };
  on("session.id", () => ({ value: "wait-main" }));
  on("session.start", (_$, e) => ({ cwd: e.cwd }));
  on("session.messages", () => ({ value: [] }));
  on("session.surfaces", () => ({ value: ["terminal"] }));
  on("session.usage", () => ({ value: { startedAt: 0, context: { tokens: usage.percent * 10_000, window: options.window ?? 1_000_000, percent: usage.percent }, rateLimits: [] } }));
  on("ui.log", (_$, e) => { calls.diagnostics.push(e.text); return { value: undefined }; });
  on("tool.register", (_$, e) => ({ value: { tool: e.name } }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("turn.complete", () => ({ text: "" }));
  on("tool.call", { tool: "Bash" }, () => ({ result: "bashResult" in options ? options.bashResult : { backgroundTaskId: task.id } }));
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

test("turn実行中に受け付けた待機予約は完了通知が先着しても失われない", async ($, on) => {
  const { clock, calls } = await waitingEngine($, on);
  await $.turn.start({ text: "待機を開始", turnId: MAIN_TURN.turnId });
  await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
  await clock.advance(0);
  expect(calls.queries.length).toBe(1);
  expect(calls.compactions).toBe(0);
  await $.session.append({ door: "delivery", origin: { kind: "engine" }, uuid: "done", message: { type: "user", role: "user", content: [{ type: "text", text: "<task-notification><task-id>wait-task</task-id><status>completed</status></task-notification>" }] } });
  await $.turn.complete(MAIN_TURN);
  await clock.advance(0);
  expect(calls.compactions).toBe(1);
});

for (const scenario of [
  // 閾値は400,000トークンと上限の40%の大きい方。tokensはpercentの1万倍として与える。
  { command: "wait_ci.py", percent: 40, window: 1_000_000, expected: 1 },
  { command: "wait_ci.py", percent: 79.9999, window: 2_000_000, expected: 0 },
  { command: "wait_ci.py", percent: 80, window: 2_000_000, expected: 1 },
  { command: "wait_ci.py", percent: 19.9999, window: 200_000, expected: 0 },
  { command: "atk agents wait", percent: Number.NaN, window: 1_000_000, expected: 0 },
]) {
  test(`背景Bashとモデル上限に応じた圧縮: ${JSON.stringify(scenario)}`, async ($, on) => {
    const { clock, calls } = await waitingEngine($, on, scenario);
    await $.tool.call({ tool: "Bash", command: scenario.command, run_in_background: true });
    await clock.advance(60_000);
    await clock.advance(0);
    expect(calls.compactions).toBe(scenario.expected);
    if (Number.isNaN(scenario.percent)) expect(calls.diagnostics.join(" ")).toMatch("usage_unknown");
  });
}

for (const mode of ["long", "low", "ineligible-wait-owner", "running", "delegated", "subagent", "skip", "failure"] as const) {
  test(`背景waitの自動圧縮条件: ${mode}`, async ($, on) => {
    const options = { percent: mode === "low" ? 39 : mode === "long" ? 40 : 60, eligible: mode !== "ineligible-wait-owner", delegated: mode === "delegated", ...(mode === "skip" || mode === "failure" ? { compact: mode } : {}) };
    const { clock, calls } = await waitingEngine($, on, options);
    if (mode === "running") await $.turn.start({ text: "待機を開始", turnId: MAIN_TURN.turnId });
    await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true, ...(mode === "subagent" ? { agentId: "child" } : {}) });
    await clock.advance(0);
    if (mode === "running") {
      expect(calls.queries.length).toBe(1);
      expect(calls.compactions).toBe(0);
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
    await clock.advance(60_000);
    expect(calls.compactions).toBe(eligible ? 1 : 0);
    expect(calls.notices.length).toBe(mode === "skip" || mode === "failure" ? 1 : 0);
    if (mode === "low") expect(calls.diagnostics.join(" ")).toMatch("below_threshold");
    if (mode === "ineligible-wait-owner") expect(calls.diagnostics.join(" ")).toMatch("ownership_unconfirmed");
  });
}

for (const door of ["delivery", "queue"] as const) {
  for (const status of ["completed", "failed", "killed"]) {
    test(`予約判定前の${door}の${status}通知では終了した背景waitを圧縮しない`, async ($, on) => {
      const { clock, calls } = await waitingEngine($, on);
      await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
      await $.session.append({ door, origin: { kind: "engine" }, uuid: "done", message: { type: "user", role: "user", content: [{ type: "text", text: `<task-notification><task-id>wait-task</task-id><status>${status}</status></task-notification>` }] } });
      await clock.advance(0);
      expect(calls.queries.length).toBe(0);
      expect(calls.compactions).toBe(0);
    });
  }
}

test("高利用率の新しい待機でも反復せず、閾値未満を観測した後の新作業で再開する", async ($, on) => {
  const { clock, calls, usage, task } = await waitingEngine($, on);
  await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
  await clock.advance(60_000);
  await clock.advance(0);
  expect(calls.compactions).toBe(1);
  task.id = "second-wait";
  await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
  await clock.advance(60_000);
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
  on("session.surfaces", () => ({ value: ["terminal"] }));
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
  on("session.surfaces", () => ({ value: ["terminal"] }));
  on("session.compact", () => { calls++; return { skip: "状態保存待ち" }; });
  on("ui.toast", (_$, e) => { notes.push(e.text); return { value: undefined }; });
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(notes.length).toBe(1);
  expect(notes[0]).toMatch("見送られた");
  expect(notes[0]).toMatch("通常の作業を続け");
  expect(notes[0]).toMatch("compact_conversationを引数なしで再予約せよ");
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toBe(2);
});

test("受付と重複の応答後に一度だけ圧縮し、完了後は再予約できる", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  const calls: number[] = [];
  on("session.surfaces", () => ({ value: ["terminal"] }));
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
  on("session.surfaces", () => ({ value: ["terminal"] }));
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
  expect(prompts[0]).toMatch("compact_conversationを引数なしで再予約せよ");
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toBe(2);
  expect(prompts.length).toBe(1);
});

test("headlessではcompactコマンドを使い、実行拒否の後も再予約できる", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  const commands: string[] = [];
  const notices: string[] = [];
  let directCalls = 0;
  on("session.surfaces", () => ({ value: [] }));
  on("session.compact", () => { directCalls++; return { messages: [] }; });
  on("command.run", (_$, e) => {
    commands.push(e.command);
    if (commands.length === 1) throw new Error("コマンド実行拒否");
    return {};
  });
  on("ui.toast", (_$, e) => { notices.push(e.text); return { value: undefined }; });
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(notices.length).toBe(1);
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(commands).toEqual(["compact", "compact"]);
  expect(directCalls).toBe(0);
  expect(notices.length).toBe(1);
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

const DECISIONS_PATH = "/home/tester/.claude/agent-toolkit-function-hooks/compaction-wait-main.txt";

function decisions(records: Map<string, string>): { task: string; reason: string; tokens?: number; window?: number; pending: boolean }[] {
  const text = records.get(DECISIONS_PATH);
  return text === undefined ? [] : (JSON.parse(text) as { decisions: { task: string; reason: string; tokens?: number; window?: number; pending: boolean }[] }).decisions;
}

for (const scenario of [
  { mode: "ratio-below", percent: 79, window: 2_000_000, reason: "below_threshold", compactions: 0 },
  { mode: "absolute-below", percent: 39, window: 1_000_000, reason: "below_threshold", compactions: 0 },
  { mode: "eligible", percent: 61, window: 1_000_000, reason: "scheduled", compactions: 1 },
  { mode: "ineligible", percent: 61, window: 1_000_000, reason: "ownership_unconfirmed", compactions: 0 },
  { mode: "pending", percent: 61, window: 1_000_000, reason: "pending", compactions: 1 },
  { mode: "finished", percent: 61, window: 1_000_000, reason: "wait_finished", compactions: 0 },
  { mode: "delegated", percent: 61, window: 1_000_000, reason: "delegated_session", compactions: 0 },
] as const) {
  test(`待機圧縮の判定理由を入力と状態つきで状態ファイルへ残す: ${scenario.mode}`, async ($, on) => {
    const { clock, calls } = await waitingEngine($, on, {
      percent: scenario.percent,
      window: scenario.window,
      eligible: scenario.mode !== "ineligible",
      delegated: scenario.mode === "delegated",
    });
    if (scenario.mode === "pending") {
      // 明示予約がturn中に残る状態で待機を始め、二重に予約しないことと理由を確かめる。
      await $.turn.start({ text: "予約して待機", turnId: MAIN_TURN.turnId });
      await $.tool.call({ tool: TOOL });
    }
    await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
    if (scenario.mode === "finished") {
      await $.session.append({ door: "delivery", origin: { kind: "engine" }, uuid: "done", message: { type: "user", role: "user", content: [{ type: "text", text: "<task-notification><task-id>wait-task</task-id><status>completed</status></task-notification>" }] } });
    }
    await clock.advance(0);
    if (scenario.mode === "pending") await $.turn.complete(MAIN_TURN);
    await clock.advance(0);
    expect(calls.compactions).toBe(scenario.compactions);
    const recorded = decisions(calls.records);
    const last = recorded.at(-1);
    expect(last?.task).toBe("wait-task");
    expect(last?.reason).toBe(scenario.reason);
    if (["ratio-below", "absolute-below", "eligible", "ineligible"].includes(scenario.mode)) {
      expect(last?.tokens).toBe(scenario.percent * 10_000);
      expect(last?.window).toBe(scenario.window);
    }
    if (scenario.mode === "pending") expect(last?.pending).toBe(true);
  });
}

for (const scenario of [
  { mode: "missing", background: true, bashResult: { stdout: "" }, reason: "background_id_missing" },
  { mode: "invalid", background: true, bashResult: { backgroundTaskId: 42 }, reason: "background_id_invalid" },
  { mode: "empty", background: true, bashResult: { backgroundTaskId: "" }, reason: "background_id_invalid" },
  { mode: "foreground", background: false, bashResult: { stdout: "" }, reason: undefined },
] as const) {
  test(`背景待機IDを検出できない戻り値を記録する: ${scenario.mode}`, async ($, on) => {
    const { clock, calls } = await waitingEngine($, on, { percent: 61, bashResult: scenario.bashResult });
    await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: scenario.background });
    await clock.advance(0);
    expect(calls.compactions).toBe(0);
    expect(calls.queries.length).toBe(0);
    const recorded = decisions(calls.records);
    expect(recorded.at(-1)?.reason).toBe(scenario.reason);
    if (scenario.reason !== undefined) expect(recorded.at(-1)?.task).toBe("unknown");
  });
}

test("判定記録は直近20件に限る", async ($, on) => {
  const { clock, calls, task } = await waitingEngine($, on, { percent: 10 });
  for (let index = 0; index < 25; index++) {
    task.id = `wait-${index}`;
    await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
    await clock.advance(0);
  }
  const recorded = decisions(calls.records);
  expect(recorded.length).toBe(20);
  expect(recorded[0]?.task).toBe("wait-5");
});

test("状態ファイルを書けない場合も待機時圧縮を続ける", async ($, on) => {
  const { clock, calls } = await waitingEngine($, on, { percent: 61, writeFails: true });
  await $.tool.call({ tool: "Bash", command: "atk agents wait", run_in_background: true });
  await clock.advance(0);
  await clock.advance(0);
  expect(calls.compactions).toBe(1);
  expect(calls.diagnostics.join(" ")).toMatch("scheduled");
});
