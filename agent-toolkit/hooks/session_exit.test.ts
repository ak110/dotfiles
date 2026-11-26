import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";
import { PERIODIC_RECHECK_MARKER } from "./periodic_recheck.ts";

// 終了要求を受けた`turn.complete`が、セッション限りのcron taskを削除してから`/exit`を実行することを検証する。
// cron taskが残ったまま`/exit`を実行すると、無人の常駐セッションが確認画面で止まる。

type Job = { id: string; durable?: boolean; prompt?: string };

type Options = {
  completion?: string;
  completionSurfaces?: ("terminal" | "desktop")[];
  request?: string;
  jobs?: Job[];
  decisions?: Record<string, "allow" | "ask" | "deny">;
  listThrows?: boolean;
  listResponse?: "error" | "deny";
  recheck?: string;
  deleteResponse?: "error" | "deny";
  tools?: string[];
  exitThrows?: boolean;
};

const REQUEST_PATH = "/home/tester/.claude/agent-toolkit-function-hooks/request-sess-1.txt";
const RECHECK_PATH = "/home/tester/.claude/agent-toolkit-function-hooks/recheck-sess-1.txt";
const LAUNCH = "a".repeat(32);
const COMPLETION_PATH = `/home/tester/.claude/agent-toolkit-function-hooks/completion-exit-${LAUNCH}.txt`;
const COMMON_PROMPT = `${PERIODIC_RECHECK_MARKER}\n共通の定期再確認\n</atk-auto>`;

function engine(on: On, options: Options): { calls: string[]; writes: Map<string, string>; checks: string[]; argv: (readonly string[])[] } {
  const calls: string[] = [];
  const checks: string[] = [];
  const writes = new Map<string, string>();
  const argv: (readonly string[])[] = [];
  on("session.id", () => ({ value: "sess-1" }));
  if (options.completion !== undefined) {
    on("session.surfaces", () => ({ value: options.completionSurfaces ?? ["terminal"] }));
  }
  on("env.get", (_$, e) => ({
    value: e.name === "HOME" ? "/home/tester" :
      e.name === "AGENT_TOOLKIT_COMPLETION_LAUNCH_ID" && options.completion !== undefined ? LAUNCH : undefined,
  }));
  on("fs.exists", (_$, e) => ({ value: e.path === COMPLETION_PATH && options.completion !== undefined }));

  on("fs.read", (_$, e) => {
    if (e.path === COMPLETION_PATH && options.completion !== undefined) {
      return { value: writes.get(e.path) ?? JSON.stringify({ session: "sess-1", call: "tool-1", status: options.completion }) };
    }

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
  on("tool.call", { tool: "CronCreate" }, () => {
    calls.push("CronCreate");
    return { result: { id: "mounted" } };
  });
  on("tool.call", { tool: "CronDelete" }, (_$, e) => {
    calls.push(`CronDelete:${e.id}`);
    if (options.deleteResponse === "deny") return { deny: "削除が拒否された" };
    if (options.deleteResponse === "error") return { isError: true as const, result: "削除エラー", text: "削除エラー" };
    return { result: { id: e.id } };
  });
  on("process.run", (_$, e) => { argv.push(e.argv); return { value: { exitCode: 0, stdout: JSON.stringify({ cron: "*/30 * * * *", prompt: COMMON_PROMPT }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }; });
  on("command.run", (_$, e) => {
    calls.push(`command:${e.command}`);
    if (options.exitThrows) throw new Error("exit failed");
    return {};
  });
  on("turn.complete", (_$, e) => ({ text: e.answer }));
  return { calls, writes, checks, argv };
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
  const { calls, writes, argv } = engine(on, {
    recheck: "complete",
    jobs: [{ id: "common", prompt: COMMON_PROMPT }, { id: "other" }, { id: "durable", prompt: COMMON_PROMPT, durable: true }, { id: "obligation", prompt: COMMON_PROMPT.replace("</atk-auto>", "期限監視\n</atk-auto>") }],
  });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronDelete:common"]);
  expect(writes.get(RECHECK_PATH)).toBe("consumed");
  expect(argv[0]?.slice(0, 8)).toEqual(["uv", "run", "--no-cache", "--no-config", "--no-project", "--python", "3.15", "python"]);
  expect(argv[0]?.[8]?.endsWith("/scripts/launch.py")).toBe(true);
  expect(argv[0]?.[9]).toBe(argv[0]?.[8]?.replace(/\/scripts\/launch\.py$/, "/agent_toolkit/atk.py"));
  expect(argv[0]?.slice(10)).toEqual(["wait-schedule"]);
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

test("背景だけの待機では定期確認を1回作る", async ($, on) => {
  const { calls } = engine(on, { recheck: "pending-background", tools: ["CronList", "CronDelete", "CronCreate"] });
  await $.turn.complete(MAIN_TURN);
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronCreate"]);
});

test("既存の定期確認があれば背景待ちで増やさない", async ($, on) => {
  const { calls } = engine(on, { recheck: "pending-background", jobs: [{ id: "common", prompt: COMMON_PROMPT }] });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList"]);
});

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
    expect(prompts[0]).toContain("次にターンを終えるとき");
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

test("回収の同一失敗を省き、未点検・対象変更・解消後の再発を区別する", async ($, on) => {
  const clock = mock.clock(on);
  const prompts: string[] = [];
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  const options: Options = { recheck: "complete", deleteResponse: "deny", jobs: [{ id: "common", prompt: COMMON_PROMPT }] };
  const { writes } = engine(on, options);
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  await $.turn.complete(MAIN_TURN);
  writes.set(RECHECK_PATH, "complete");
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  expect(prompts.length).toBe(1);
  options.jobs = [{ id: "other", prompt: COMMON_PROMPT }];
  writes.set(RECHECK_PATH, "complete");
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  options.jobs = [{ id: "common", prompt: COMMON_PROMPT }];
  writes.set(RECHECK_PATH, "complete");
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  expect(prompts.length).toBe(3);
  options.deleteResponse = undefined;
  writes.set(RECHECK_PATH, "complete");
  await $.turn.complete(MAIN_TURN);
  options.deleteResponse = "deny";
  writes.set(RECHECK_PATH, "complete");
  await $.turn.complete(MAIN_TURN);
  await clock.advance(1);
  expect(prompts.length).toBe(4);
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
  expect(prompts[0]).toContain("次にターンを終えるとき");
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

// Stopが全待機の終了を判定した時点の回収要求（`periodic_recheck_collect`）を、modの要求処理へ読ませる。
function collectRequest(on: On, options: { deleteDecision?: "allow" | "deny" } = {}) {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  const id = "c".repeat(32);
  const calls: string[] = [];
  const responses: { id: string; ok: boolean; reason: string }[] = [];
  const prompts: string[] = [];
  on("session.id", () => ({ value: "sess-1" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("fs.list", () => ({ value: [{ name: `ui-request-sess-1-${id}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false }] }));
  on("fs.exists", () => ({ value: true }));
  on("fs.read", () => ({ value: JSON.stringify({ id, operation: "periodic_recheck_collect", input: {}, expires: 99999999999 }) }));
  on("fs.write", (_$, e) => {
    if (e.path.includes("/ui-response-")) responses.push(JSON.parse(e.text));
    return { value: undefined };
  });
  on("tool.list", () => ({ value: ["CronList", "CronDelete"].map((name) => ({ name, description: name, mcp: false })) }));
  on("tool.check", (_$, e) => ({ decision: e.tool === "CronDelete" ? options.deleteDecision ?? "allow" : "allow" }));
  on("tool.call", { tool: "CronList" }, () => {
    calls.push("CronList");
    return { result: { jobs: [{ id: "common", prompt: COMMON_PROMPT }, { id: "obligation", prompt: COMMON_PROMPT.replace("</atk-auto>", "期限監視\n</atk-auto>") }] } };
  });
  on("tool.call", { tool: "CronDelete" }, (_$, e) => { calls.push(`CronDelete:${e.id}`); return { result: { id: e.id } }; });
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ cron: "*/30 * * * *", prompt: COMMON_PROMPT }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  return { clock, calls, responses, prompts };
}

test("Stopの回収要求は共通promptのtaskだけを削除して応答する", async ($, on) => {
  const { clock, calls, responses, prompts } = collectRequest(on);
  await $.turn.start({ text: "作業", turnId: "t1" });
  await clock.advance(100);
  expect(calls).toEqual(["CronList", "CronDelete:common"]);
  expect(responses).toEqual([expect.objectContaining({ ok: true, reason: "" })]);
  expect(prompts).toEqual([]);
});

test("Stopの回収要求で削除できなければ理由を応答へ返し、次のターンを始めない", async ($, on) => {
  const { clock, calls, responses, prompts } = collectRequest(on, { deleteDecision: "deny" });
  await $.turn.start({ text: "作業", turnId: "t1" });
  await clock.advance(100);
  expect(calls).toEqual(["CronList"]);
  expect(responses[0]?.reason).toContain("common: deny");
  expect(prompts).toEqual([]);
});

test("協調の終了要求はStopが許可した時だけ実行し、コマンドが戻っても終了後の表示のためexitingを残す", async ($, on) => {
  const { calls, writes } = engine(on, { completion: "approved" });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronList", "command:exit"]);
  expect(JSON.parse(writes.get(COMPLETION_PATH) ?? "{}").status).toBe("exiting");
});

test("協調の終了要求は/exitが例外になれば取り消す", async ($, on) => {
  const { calls, writes } = engine(on, { completion: "approved", exitThrows: true });
  await $.turn.complete(MAIN_TURN);
  expect(calls).toEqual(["CronList", "CronList", "command:exit"]);
  expect(writes.get(COMPLETION_PATH)).toBe("{}");
});

test("協調の終了要求は同じターンのStopの許可が無い時に取り消す", async ($, on) => {
  const blocked = engine(on, { completion: "requested" });
  await $.turn.complete(MAIN_TURN);
  expect(blocked.calls).not.toContain("command:exit");
  expect(blocked.writes.get(COMPLETION_PATH)).toBe("{}");
});

test("協調の終了要求は中断した時に取り消す", async ($, on) => {
  const { calls, writes } = engine(on, { completion: "approved" });
  await $.turn.complete({ ...MAIN_TURN, isAborted: true });
  expect(calls).not.toContain("command:exit");
  expect(writes.get(COMPLETION_PATH)).toBe("{}");
});

test("協調の終了要求は無関係なセッション限りのcronを消さず取り消す", async ($, on) => {
  const { calls, writes } = engine(on, { completion: "approved", jobs: [{ id: "user-loop" }] });
  await $.turn.complete(MAIN_TURN);
  expect(calls).not.toContain("command:exit");
  expect(calls).not.toContain("CronDelete:user-loop");
  expect(writes.get(COMPLETION_PATH)).toBe("{}");
});

test("協調の終了要求は次のターン開始時に破棄し、遅い完了イベントで実行しない", async ($, on) => {
  const { calls, writes } = engine(on, { completion: "approved" });
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  await $.turn.start({ text: "続けて", turnId: "t2" });
  await $.turn.complete(MAIN_TURN);
  expect(calls).not.toContain("command:exit");
  expect(writes.get(COMPLETION_PATH)).toBe("{}");
});

test("協調の終了要求はcron一覧を取得できなければ取り消す", async ($, on) => {
  const { calls, writes } = engine(on, { completion: "approved", listThrows: true });
  await $.turn.complete(MAIN_TURN);
  expect(calls).not.toContain("command:exit");
  expect(writes.get(COMPLETION_PATH)).toBe("{}");
});

test("協調の終了要求は次のturn.startより前でもユーザーの入力で取り消す", async ($, on) => {
  const { calls, writes } = engine(on, { completion: "approved", completionSurfaces: [] });
  const input: string[] = [];
  on("prompt.submit", (_$, e) => { input.push(e.text); return { text: e.text }; });
  await $.prompt.submit({ text: "作業を続けて", wait: false, origin: { kind: "composer" } });
  expect(input).toEqual(["作業を続けて"]);
  expect(writes.get(COMPLETION_PATH)).toBe("{}");
  await $.turn.complete(MAIN_TURN);
  expect(calls).not.toContain("command:exit");
  expect(writes.get(COMPLETION_PATH)).toBe("{}");
});

for (const command of ["Bash", "agents-server委譲"]) {
  test(`Stop許可後に${command}の待機を検出したら協調の終了を取り消す`, async ($, on) => {
    const { calls, writes } = engine(on, { completion: "approved" });
    on("state.get", { plugin: "agent-toolkit", key: "backgroundWaits" }, () => ({ value: { value: { task: { id: "task", command } }, version: 1 } }));
    await $.turn.complete(MAIN_TURN);
    expect(calls).not.toContain("command:exit");
    expect(writes.get(COMPLETION_PATH)).toBe("{}");
  });
}


for (const surfaces of [[], ["desktop"], ["terminal", "desktop"]] satisfies Options["completionSurfaces"][]) {
  test(`端末だけの対話画面でなければ協調の終了を取り消す: ${JSON.stringify(surfaces)}`, async ($, on) => {
    const { calls, writes } = engine(on, { completion: "approved", completionSurfaces: surfaces });
    await $.turn.complete(MAIN_TURN);
    expect(calls).toEqual([]);
    expect(writes.get(COMPLETION_PATH)).toBe("{}");
  });
}
