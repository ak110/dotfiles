import type { On } from "claude-code";
import { expect, test } from "claude-code/testing";

// 終了要求を受けた`turn.complete`が、セッション限りのcron taskを削除してから`/exit`を実行することを検証する。
// cron taskが残ったまま`/exit`を実行すると、無人の常駐セッションが確認画面で止まる。

type Job = { id: string; durable?: boolean };

type Options = {
  request?: string;
  jobs?: Job[];
  decisions?: Record<string, "allow" | "ask" | "deny">;
  listThrows?: boolean;
};

const REQUEST_PATH = "/home/tester/.claude/agent-toolkit-function-hooks/request-sess-1.txt";

function engine(on: On, options: Options): { calls: string[]; writes: Map<string, string> } {
  const calls: string[] = [];
  const writes = new Map<string, string>();
  on("session.id", () => ({ value: "sess-1" }));
  on("env.get", (_$, e) => ({ value: e.name === "HOME" ? "/home/tester" : undefined }));
  on("fs.read", (_$, e) => {
    if (e.path !== REQUEST_PATH || options.request === undefined) {
      throw Object.assign(new Error(`ENOENT: ${e.path}`), { code: "ENOENT" });
    }
    return { value: options.request };
  });
  on("fs.write", (_$, e) => {
    writes.set(e.path, e.text);
    return { value: undefined };
  });
  on("tool.check", (_$, e) => ({ decision: options.decisions?.[e.tool] ?? "allow" }));
  on("tool.call", { tool: "CronList" }, () => {
    calls.push("CronList");
    if (options.listThrows) throw new Error("CronList failed");
    return { result: { jobs: (options.jobs ?? []).map((job) => ({ cron: "*/30 * * * *", humanSchedule: "Every 30 minutes", prompt: "p", ...job })) } };
  });
  on("tool.call", { tool: "CronDelete" }, (_$, e) => {
    calls.push(`CronDelete:${e.id}`);
    return { result: { id: e.id } };
  });
  on("command.run", (_$, e) => {
    calls.push(`command:${e.command}`);
    return {};
  });
  on("turn.complete", () => ({ text: "" }));
  return { calls, writes };
}

const MAIN_TURN = { answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" as const };

test("終了要求があれば非durableのcron taskだけを削除してから/exitを実行する", async ($, on) => {
  const { calls, writes } = engine(on, {
    request: "requested",
    jobs: [{ id: "a1", durable: false }, { id: "b2", durable: true }, { id: "c3" }],
  });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronDelete:a1", "CronDelete:c3", "command:exit"]);
  expect(writes.get(REQUEST_PATH)).toBe("consumed");
});

test("許可がallowでなければ削除せずに/exitを実行する", async ($, on) => {
  const { calls } = engine(on, {
    request: "requested",
    jobs: [{ id: "a1" }],
    decisions: { CronDelete: "ask" },
  });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "command:exit"]);
});

test("一覧の取得が許可されなければ一覧を呼ばずに/exitを実行する", async ($, on) => {
  const { calls } = engine(on, { request: "requested", jobs: [{ id: "a1" }], decisions: { CronList: "deny" } });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["command:exit"]);
});

test("一覧の取得が例外になっても/exitを実行する", async ($, on) => {
  const { calls } = engine(on, { request: "requested", listThrows: true });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "command:exit"]);
});

test("終了要求がrequestedでなければ一覧も/exitも呼ばない", async ($, on) => {
  const { calls } = engine(on, { request: "consumed", jobs: [{ id: "a1" }] });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual([]);
});
