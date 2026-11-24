import type { On, SessionRateLimit } from "claude-code";
import { type Engine, expect, mock, test } from "claude-code/testing";

// 入力はClaude Codeの型宣言SessionRateLimit、TurnCompleteInput、StopFailureHookInputに従う。
const TURN = { answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" as const };

async function engine($: Engine, on: On, windows: SessionRateLimit[], env: Record<string, string> = {}, now?: { value: number }, request?: { value: string }, server?: { value: string }) {
  mock.env(on, env);
  let release: (() => void) | undefined;
  let cleared: (() => void) | undefined;
  const completed = new Promise<void>((resolve) => { cleared = resolve; });
  const clock = now === undefined ? mock.clock(on) : {
    advance: async (_ms: number) => { release?.(); await completed; },
  };
  if (now !== undefined) {
    on("clock.now", () => ({ value: now.value }));
    on("clock.after", async () => {
      await new Promise<void>((resolve) => { release = resolve; });
      return { value: undefined };
    });
  }
  const prompts: string[] = [];
  const statuses: (string | undefined)[] = [];
  on("session.usage", () => ({ value: { startedAt: 0, context: { tokens: 0, window: 1, percent: 0 }, rateLimits: windows } }));
  on("session.start", (_$, e) => ({ cwd: e.cwd }));
  on("session.id", () => ({ value: "usage-test" }));
  const requestAt = (path: string) => path.includes("rate-limit-") ? request : path.includes("server-error-") ? server : undefined;
  on("fs.write", (_$, e) => {
    const file = requestAt(e.path);
    if (file) file.value = e.text;
    return { value: undefined };
  });
  on("fs.read", (_$, e) => ({ value: requestAt(e.path)?.value ?? "consumed" }));
  on("tool.register", (_$, e) => ({ value: { tool: e.name } }));
  on("turn.complete", () => ({ text: "" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("ui.status", (_$, e) => { statuses.push(e.text); if (e.text === undefined) cleared?.(); return { value: undefined }; });
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  await $.session.start({ cwd: "/work", surface: null, isInteractive: true });
  return { clock, prompts, statuses };
}

test("猶予後終了は最大使用窓の解除後へ単一予約を更新し表示を回収する", async ($, on) => {
  const windows = [
    { kind: "five_hour", percentUsed: 100, resetsAt: "1970-01-01T00:00:01Z" },
    { kind: "seven_day", percentUsed: 101, resetsAt: "1970-01-01T00:00:02Z" },
    { kind: "spend_limit", percentUsed: 200, resetsAt: "1970-01-01T00:00:09Z" },
  ];
  const { clock, prompts, statuses } = await engine($, on, windows);
  await $.turn.complete(TURN);
  await $.turn.complete(TURN);
  await clock.advance(2000);
  expect(prompts).toEqual([]);
  await clock.advance(1);
  expect(prompts.length).toBe(1);
  expect(prompts[0]?.split("\n")[0]).toBe('<atk-auto source="usage-limit-resume" kind="notice">');
  expect(prompts[0]).toContain("最初の未完了工程から続けよ");
  expect(prompts[0]).toContain("未完了の作業が無い場合、既に続行済みの場合、ユーザーが中止を指示している場合は、何もせず終えよ");
  const reads = [...prompts[0]!.matchAll(/("(?:[^"\\]|\\.)*")を読み/g)];
  expect(reads.length).toBe(1);
  const document: string = JSON.parse(reads[0]![1]!);
  expect(document).toMatch(/^(?:\/|[A-Za-z]:[\\/]|\\\\)/);
  expect(document.endsWith("/skills/atk-mode/references/usage-limit.md")).toBe(true);
  expect(statuses[0]).toMatch("1970-01-01T00:00:02.001Z");
  expect(statuses.at(-1)).toBe(undefined);
  await clock.advance(10_000);
  expect(prompts.length).toBe(1);
});

test("新しい上限の契機は予約時刻を置き換える", async ($, on) => {
  const windows = [{ kind: "five_hour", percentUsed: 100, resetsAt: "1970-01-01T00:00:01Z" }];
  const { clock, prompts } = await engine($, on, windows);
  await $.turn.complete(TURN);
  windows[0]!.resetsAt = "1970-01-01T00:00:02Z";
  await $.turn.complete(TURN);
  await clock.advance(1001);
  expect(prompts).toEqual([]);
  await clock.advance(1000);
  expect(prompts.length).toBe(1);
});

for (const resetsAt of [undefined, "1970-01-01T00:00:00Z", "invalid"]) {
  test(`解除時刻${String(resetsAt)}は300秒ごとに利用枠を確かめてから投入する`, async ($, on) => {
    const windows = [{ kind: "seven_day", percentUsed: 100, resetsAt }];
    const { clock, prompts, statuses } = await engine($, on, windows);
    await $.turn.complete(TURN);
    await clock.advance(299_999);
    expect(prompts).toEqual([]);
    await clock.advance(1);
    expect(prompts).toEqual([]);
    windows[0]!.percentUsed = 0;
    await clock.advance(300_000);
    expect(prompts.length).toBe(1);
    expect(statuses.at(-1)).toBe(undefined);
  });
}

test("解除時刻後に始まったメインturnへ重複投入しない", async ($, on) => {
  // timerが遅れて処理されるホストを再現し、turn.startを先に配送する。
  const now = { value: 0 };
  const { clock, prompts, statuses } = await engine($, on, [{ kind: "five_hour", percentUsed: 100, resetsAt: "1970-01-01T00:00:01Z" }], {}, now);
  await $.turn.complete(TURN);
  now.value = 2000;
  await $.turn.start({ text: "続けて", turnId: "t2" });
  await clock.advance(1001);
  expect(prompts).toEqual([]);
  expect(statuses.at(-1)).toBe(undefined);
});

const MANAGED_ENVS: Record<string, string>[] = [
  { AGENT_TOOLKIT_DELEGATED_SESSION: "1" }, { AGENT_TOOLKIT_OWNER_SESSION: "owner" },
  { AGENT_TOOLKIT_PROCESS_LOOP_SESSION: "1", AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID: "usage-test" },
  { AGENT_TOOLKIT_RUN_SKILL_ID: "run-test" },
];

test("StopFailureコマンドhookの要求は使用率未反映でも予約し一度消費する", async ($, on) => {
  const request = { value: "requested" };
  const { clock, prompts } = await engine($, on, [], { HOME: "/home/tester" }, undefined, request);
  await $.turn.complete(TURN);
  expect(request.value).toBe("consumed");
  await clock.advance(300_000);
  expect(prompts.length).toBe(1);
  await $.turn.complete(TURN);
  await clock.advance(300_000);
  expect(prompts.length).toBe(1);
});
for (const env of MANAGED_ENVS) {
  test(`管理起動${JSON.stringify(env)}は所有者へ再開を委ねる`, async ($, on) => {
    const request = { value: "requested" };
    const { clock, prompts, statuses } = await engine($, on, [{ kind: "five_hour", percentUsed: 100, resetsAt: "1970-01-01T00:00:01Z" }], { ...env, HOME: "/home/tester" }, undefined, request);
    await $.turn.complete(TURN);
    await $.turn.complete(TURN);
    await clock.advance(400_000);
    expect(prompts).toEqual([]);
    expect(statuses).toEqual([]);
    expect(request.value).toBe("requested");
  });
}

test("サブエージェントと通常終了は予約しない", async ($, on) => {
  const windows = [{ kind: "five_hour", percentUsed: 100, resetsAt: "1970-01-01T00:00:01Z" }];
  const { clock, prompts, statuses } = await engine($, on, windows);
  await $.turn.complete({ ...TURN, agentId: "child" });
  windows[0]!.percentUsed = 99.9;
  await $.turn.complete(TURN);
  await clock.advance(400_000);
  expect(prompts).toEqual([]);
  expect(statuses).toEqual([]);
});

test("サーバーエラーは1分、5分、15分後に再開し、4回目で停止する", async ($, on) => {
  const server = { value: "consumed" };
  const { clock, prompts, statuses } = await engine($, on, [], { HOME: "/home/tester" }, undefined, undefined, server);
  for (const [index, delay] of [60_000, 300_000, 900_000].entries()) {
    server.value = "requested";
    await $.turn.complete(TURN);
    expect(server.value).toBe("consumed");
    await clock.advance(delay - 1);
    expect(prompts.length).toBe(index);
    await clock.advance(1);
    expect(prompts.length).toBe(index + 1);
    expect(prompts.at(-1)).toContain("サーバー側の一時的なAPIエラー");
  }
  server.value = "requested";
  await $.turn.complete(TURN);
  expect(statuses.at(-1)).toContain("上限");
  await clock.advance(900_000);
  expect(prompts.length).toBe(3);
  await $.turn.start({ text: "復帰", turnId: "manual" });
  await $.turn.complete({ ...TURN, turnId: "manual" });
  expect(statuses.at(-1)).toBe(undefined);
});

test("サーバーエラーでないターンの終了で再開間隔を初期化する", async ($, on) => {
  const server = { value: "requested" };
  const { clock, prompts } = await engine($, on, [], { HOME: "/home/tester" }, undefined, undefined, server);
  await $.turn.complete(TURN);
  await clock.advance(60_000);
  expect(prompts.length).toBe(1);
  await $.turn.start({ text: prompts[0]!, turnId: "resumed" });
  await $.turn.complete({ ...TURN, turnId: "resumed" });
  server.value = "requested";
  await $.turn.complete(TURN);
  await clock.advance(60_000);
  expect(prompts.length).toBe(2);
});

test("待機中に別のターンが始まればサーバーエラーによる再開を取り消す", async ($, on) => {
  const server = { value: "requested" };
  const { clock, prompts, statuses } = await engine($, on, [], { HOME: "/home/tester" }, undefined, undefined, server);
  await $.turn.complete(TURN);
  await clock.advance(1000);
  await $.turn.start({ text: "中止", turnId: "t2" });
  await clock.advance(59_000);
  expect(statuses.at(-1)).toBe(undefined);
  expect(prompts).toEqual([]);
});

for (const explicitLimit of [true, false]) {
  test(`同時に成立した利用上限を優先する（要求ファイル: ${explicitLimit}）`, async ($, on) => {
    const server = { value: "requested" };
    const rate = { value: explicitLimit ? "requested" : "consumed" };
    const windows = [{ kind: "five_hour", percentUsed: explicitLimit ? 99 : 100, resetsAt: "1970-01-01T00:02:00Z" }];
    const { clock, prompts } = await engine($, on, windows, { HOME: "/home/tester" }, undefined, rate, server);
    await $.turn.complete(TURN);
    expect(server.value).toBe("consumed");
    await clock.advance(120_000);
    expect(prompts).toEqual([]);
    await clock.advance(1);
    expect(prompts.length).toBe(1);
    expect(prompts[0]).toContain('source="usage-limit-resume"');
    await clock.advance(900_000);
    expect(prompts.length).toBe(1);
  });
}

test("中止されたターンからサーバーエラーの再開を予約しない", async ($, on) => {
  const server = { value: "requested" };
  const { clock, prompts } = await engine($, on, [], { HOME: "/home/tester" }, undefined, undefined, server);
  await $.turn.complete({ ...TURN, isAborted: true });
  expect(server.value).toBe("consumed");
  await clock.advance(900_000);
  expect(prompts).toEqual([]);
});

test("子のターンが始まってもメインのサーバーエラー再開は残る", async ($, on) => {
  const server = { value: "requested" };
  const { clock, prompts } = await engine($, on, [], { HOME: "/home/tester" }, undefined, undefined, server);
  await $.turn.complete({ ...TURN, reason: "error" });
  await $.turn.start({ text: "", turnId: "child-turn", agentId: "child" });
  await clock.advance(60_000);
  expect(prompts.length).toBe(1);
});

test("turn.completeより後に届いたStopFailureの要求でも再開する", async ($, on) => {
  const server = { value: "consumed" };
  const { clock, prompts } = await engine($, on, [], { HOME: "/home/tester" }, undefined, undefined, server);
  await $.turn.complete({ ...TURN, reason: "error" });
  server.value = "requested";
  await clock.advance(250);
  expect(server.value).toBe("consumed");
  await clock.advance(60_000);
  expect(prompts.length).toBe(1);
});
