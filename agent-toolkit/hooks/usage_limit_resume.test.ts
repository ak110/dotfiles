import type { On, SessionRateLimit } from "claude-code";
import { type Engine, expect, mock, test } from "claude-code/testing";

// 入力はClaude Codeの型宣言SessionRateLimit、TurnCompleteInput、StopFailureHookInputに従う。
const TURN = { answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" as const };

async function engine($: Engine, on: On, windows: SessionRateLimit[], env: Record<string, string> = {}, now?: { value: number }) {
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
  on("fs.write", () => ({ value: undefined }));
  on("fs.read", () => ({ value: "consumed" }));
  on("tool.register", (_$, e) => ({ value: { tool: e.name } }));
  on("turn.complete", () => ({ text: "" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("classic.StopFailure", () => ({}));
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
  await $.classic.StopFailure({ error: "rate_limit" });
  await clock.advance(1001);
  expect(prompts).toEqual([]);
  await clock.advance(1000);
  expect(prompts.length).toBe(1);
});

for (const resetsAt of [undefined, "1970-01-01T00:00:00Z", "invalid"]) {
  test(`解除時刻${String(resetsAt)}は300秒ごとに利用枠を確かめてから投入する`, async ($, on) => {
    const windows = [{ kind: "seven_day", percentUsed: 100, resetsAt }];
    const { clock, prompts, statuses } = await engine($, on, windows);
    await $.classic.StopFailure({ error: "rate_limit" });
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
  await $.classic.StopFailure({ error: "rate_limit" });
  now.value = 2000;
  await $.turn.start({ text: "続けて", turnId: "t2" });
  await clock.advance(1001);
  expect(prompts).toEqual([]);
  expect(statuses.at(-1)).toBe(undefined);
});

const DELEGATED_ENVS: Record<string, string>[] = [{ AGENT_TOOLKIT_DELEGATED_SESSION: "1" }, { AGENT_TOOLKIT_OWNER_SESSION: "owner" }];
for (const env of DELEGATED_ENVS) {
  test(`委譲先${JSON.stringify(env)}は予約しない`, async ($, on) => {
    const { clock, prompts, statuses } = await engine($, on, [{ kind: "five_hour", percentUsed: 100, resetsAt: "1970-01-01T00:00:01Z" }], env);
    await $.classic.StopFailure({ error: "rate_limit" });
    await $.turn.complete(TURN);
    await clock.advance(400_000);
    expect(prompts).toEqual([]);
    expect(statuses).toEqual([]);
  });
}

test("サブエージェントと通常終了は予約しない", async ($, on) => {
  const windows = [{ kind: "five_hour", percentUsed: 100, resetsAt: "1970-01-01T00:00:01Z" }];
  const { clock, prompts, statuses } = await engine($, on, windows);
  await $.classic.StopFailure({ error: "rate_limit", agent_id: "child" });
  await $.turn.complete({ ...TURN, agentId: "child" });
  windows[0]!.percentUsed = 99.9;
  await $.turn.complete(TURN);
  await $.classic.StopFailure({ error: "server_error" });
  await clock.advance(400_000);
  expect(prompts).toEqual([]);
  expect(statuses).toEqual([]);
});
