import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";
import { PERIODIC_RECHECK_MARKER } from "./periodic_recheck.ts";

// 終了要求を受けた`turn.complete`が、セッション限りのcron taskを削除してから`/exit`を実行することを検証する。
// cron taskが残ったまま`/exit`を実行すると、無人の常駐セッションが確認画面で止まる。

type Job = { id: string; durable?: boolean; prompt?: string };

type Options = {
  request?: string;
  jobs?: Job[];
  decisions?: Record<string, "allow" | "ask" | "deny">;
  listThrows?: boolean;
  listResponse?: "error" | "deny";
  recheck?: string;
  deleteResponse?: "error" | "deny";
  tools?: string[];
};

const REQUEST_PATH = "/home/tester/.claude/agent-toolkit-function-hooks/request-sess-1.txt";
const RECHECK_PATH = "/home/tester/.claude/agent-toolkit-function-hooks/recheck-sess-1.txt";
const COMMON_PROMPT = `${PERIODIC_RECHECK_MARKER}\n共通の定期再確認\n</atk-auto>`;

function engine(on: On, options: Options): { calls: string[]; writes: Map<string, string>; checks: string[] } {
  const calls: string[] = [];
  const checks: string[] = [];
  const writes = new Map<string, string>();
  on("session.id", () => ({ value: "sess-1" }));
  on("env.get", (_$, e) => ({ value: e.name === "HOME" ? "/home/tester" : undefined }));
  on("fs.read", (_$, e) => {
    if (e.path === RECHECK_PATH && options.recheck !== undefined) return { value: writes.get(e.path) ?? options.recheck };
    if (e.path !== REQUEST_PATH) {
      throw Object.assign(new Error(`ENOENT: ${e.path}`), { code: "ENOENT" });
    }
    return { value: options.request ?? "consumed" };
  });
  on("fs.write", (_$, e) => {
    writes.set(e.path, e.text);
    return { value: undefined };
  });
  const tools = options.tools ?? ["CronList", "CronDelete"];
  on("tool.list", () => ({ value: tools.map((name) => ({ name, description: name, mcp: false })) }));
  on("tool.check", (_$, e) => {
    checks.push(e.tool);
    if (!tools.includes(e.tool)) throw new Error(`$.tool.check: no tool named "${e.tool}" in this session`);
    return { decision: options.decisions?.[e.tool] ?? "allow" };
  });
  on("tool.call", { tool: "CronList" }, () => {
    calls.push("CronList");
    if (options.listThrows) throw new Error("CronList failed");
    if (options.listResponse === "error") return { isError: true as const, result: "CronList failed", text: "CronList failed" };
    if (options.listResponse === "deny") return { deny: "CronList denied" };
    return { result: { jobs: (options.jobs ?? []).map((job) => ({ cron: "*/30 * * * *", humanSchedule: "Every 30 minutes", prompt: "p", ...job })) } };
  });
  on("tool.call", { tool: "CronDelete" }, (_$, e) => {
    calls.push(`CronDelete:${e.id}`);
    if (options.deleteResponse === "deny") return { deny: "削除が拒否された" };
    if (options.deleteResponse === "error") return { isError: true as const, result: "削除エラー", text: "削除エラー" };
    return { result: { id: e.id } };
  });
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ cron: "*/30 * * * *", prompt: COMMON_PROMPT }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  on("command.run", (_$, e) => {
    calls.push(`command:${e.command}`);
    return {};
  });
  on("turn.complete", (_$, e) => ({ text: e.answer }));
  return { calls, writes, checks };
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

test("一覧がエラー応答なら削除せずに/exitを実行する", async ($, on) => {
  const { calls } = engine(on, { request: "requested", jobs: [{ id: "a1" }], listResponse: "error" });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "command:exit"]);
});

test("一覧が拒否応答なら削除せずに/exitを実行する", async ($, on) => {
  const { calls } = engine(on, { request: "requested", jobs: [{ id: "a1" }], listResponse: "deny" });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "command:exit"]);
});

test("終了要求がrequestedでなければ一覧も/exitも呼ばない", async ($, on) => {
  const { calls } = engine(on, { request: "consumed", jobs: [{ id: "a1" }] });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual([]);
});

test("全待機終了の判定は共通promptのtaskだけを一度回収する", async ($, on) => {
  const { calls, writes } = engine(on, {
    recheck: "complete",
    jobs: [{ id: "common", prompt: COMMON_PROMPT }, { id: "other" }, { id: "durable", prompt: COMMON_PROMPT, durable: true }, { id: "obligation", prompt: COMMON_PROMPT.replace("</atk-auto>", "期限監視\n</atk-auto>") }],
  });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronDelete:common"]);
  expect(writes.get(RECHECK_PATH)).toBe("consumed");
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronDelete:common"]);
});

for (const recheck of [undefined, "pending", "unknown", "consumed", "broken"]) {
  test(`待機判定${recheck}ではtaskを保持する`, async ($, on) => {
    const { calls } = engine(on, { recheck, jobs: [{ id: "common", prompt: COMMON_PROMPT }] });
    await $.turn.complete(MAIN_TURN);
    expect(calls).toEqual([]);
  });
}

for (const deleteResponse of ["error", "deny"] as const) {
  test(`通常回収の削除${deleteResponse}は同じ会話へ理由を返す`, async ($, on) => {
    const clock = mock.clock(on);
    const prompts: string[] = [];
    on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
    const { calls } = engine(on, { recheck: "complete", deleteResponse, jobs: [{ id: "common", prompt: COMMON_PROMPT }] });
    await $.turn.complete(MAIN_TURN);
    await clock.advance(1);
    expect(calls).toEqual(["CronList", "CronDelete:common"]);
    expect(prompts[0]).toContain("common");
    expect(prompts[0]).toContain("次のStop");
  });
}

test("追加義務付きtaskは通常回収で保持し終了要求で回収する", async ($, on) => {
  const options: Options = { recheck: "complete", jobs: [{ id: "obligation", prompt: COMMON_PROMPT.replace("</atk-auto>", "測定コマンド: date +%s、閾値: 3600秒\n</atk-auto>") }] };
  const { calls } = engine(on, options);
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList"]);
  options.request = "requested";
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronList", "CronDelete:obligation", "command:exit"]);
});

test("削除拒否後も次の有効なStop判定で再評価できる", async ($, on) => {
  const clock = mock.clock(on);
  const prompts: string[] = [];
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  const options: Options = { recheck: "complete", deleteResponse: "deny", jobs: [{ id: "common", prompt: COMMON_PROMPT }] };
  const { calls, writes } = engine(on, options);
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  options.deleteResponse = undefined;
  writes.set(RECHECK_PATH, "complete");
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronDelete:common", "CronList", "CronDelete:common"]);
  expect(prompts.length).toBe(1);
});

test("通常回収の削除許可がaskなら呼び出さず理由を届ける", async ($, on) => {
  const clock = mock.clock(on);
  const prompts: string[] = [];
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  const { calls } = engine(on, { recheck: "complete", decisions: { CronDelete: "ask" }, jobs: [{ id: "common", prompt: COMMON_PROMPT }] });
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  expect(calls).toEqual(["CronList"]);
  expect(prompts[0]).toContain("common: ask");
});

test("CronList非公開の探索は回収を適用せず、正常な最終返却を通知応答で上書きしない", async ($, on) => {
  const clock = mock.clock(on);
  const answer = "調査結果を返す。";
  let latest = answer;
  const prompts: string[] = [];
  on("prompt.submit", (_$, e) => { prompts.push(e.text); latest = "警告への応答"; return { text: latest }; });
  const { calls, checks } = engine(on, { recheck: "complete", tools: ["Read", "Glob", "Grep", "Bash"] });
  const result = await $.turn.complete({ ...MAIN_TURN, answer });
  await clock.advance(1);
  expect(result.text).toBe(answer);
  expect(latest).toBe(answer);
  expect(checks).toEqual([]);
  expect(calls).toEqual([]);
  expect(prompts).toEqual([]);
});

test("所有taskを確認した後のCronDelete非公開は対象IDを含む本当の回収失敗として通知する", async ($, on) => {
  const clock = mock.clock(on);
  const prompts: string[] = [];
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  const { calls } = engine(on, { recheck: "complete", tools: ["CronList"], jobs: [{ id: "common", prompt: COMMON_PROMPT }] });
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  expect(calls).toEqual(["CronList"]);
  expect(prompts[0]).toContain("common");
  expect(prompts[0]).toContain("次のStop");
});

test("CronListが公開されても所有taskが無い通常終了は通知を作成せず結果を保持する", async ($, on) => {
  const clock = mock.clock(on);
  const prompts: string[] = [];
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "警告への応答" }; });
  const { calls } = engine(on, { recheck: "complete", jobs: [] });
  const result = await $.turn.complete({ ...MAIN_TURN, answer: "正常な調査結果" });
  await clock.advance(1);
  expect(calls).toEqual(["CronList"]);
  expect(prompts).toEqual([]);
  expect(result.text).toBe("正常な調査結果");
});
