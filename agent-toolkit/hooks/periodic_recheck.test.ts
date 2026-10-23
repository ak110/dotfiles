import type { On } from "claude-code";
import { expect, test } from "claude-code/testing";

import { parseCronJobs, PERIODIC_RECHECK_MARKER } from "./periodic_recheck.ts";

// `agents_server`の`start`の後に、メインの定期再確認のtaskを1件だけ作成し、結果を会話へ届けることを検証する。
// 装着しないと、モデルが起動前の手順を省いた場合に停滞と外部ジョブの終了を検出する主体が欠ける。
// サブエージェントへ装着すると、taskのpromptが親の会話へ届き、サブエージェントの待機を再確認しないtaskが残る。

const START = "mcp__plugin_agent-toolkit_agents_server__start" as const;
const START_TEXT = '{"session_id": "s1", "status": "running"}';

type Job = { id: string; prompt: string };

// `atk wait-schedule --format json`が返すpromptの代わり。本文の内容はPython側の定義が持ち、modは受け取った本文をそのまま渡す。
const SCHEDULE_PROMPT = `${PERIODIC_RECHECK_MARKER}\n定期再確認の発火である。\n</atk-auto>`;

type Options = {
  jobs?: Job[];
  decisions?: Record<string, "allow" | "ask" | "deny">;
  cron?: string;
  prompt?: string;
  stdout?: string;
  scheduleExitCode?: number;
  createError?: string;
  createdId?: string;
  deleteUnavailable?: boolean;
  listError?: string;
  listThrows?: boolean;
  sessionId?: string;
};

type Recorded = { calls: string[]; argv: (readonly string[])[]; created: { cron: string; prompt: string; recurring?: boolean }[]; files: Map<string, string> };

function engine(on: On, options: Options): Recorded {
  const recorded: Recorded = { calls: [], argv: [], created: [], files: new Map() };
  const jobs = [...(options.jobs ?? [])];
  // 試験の途中で書き換えた値を各呼び出しが読むよう、optionsを呼び出しの時点で参照する。
  on("session.id", () => ({ value: options.sessionId ?? "life-1" }));
  on("session.messages", () => ({ value: [] }));
  on("env.get", (_$, e) => ({ value: e.name === "HOME" ? "/home/tester" : undefined }));
  on("fs.read", (_$, e) => ({ value: recorded.files.get(e.path) ?? "consumed" }));
  on("fs.write", (_$, e) => { recorded.files.set(e.path, e.text); return { value: undefined }; });
  on("turn.complete", () => ({ text: "" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("tool.list", () => ({ value: ["CronList", "CronCreate", "CronDelete"].map((name) => ({ name, description: name, mcp: false })) }));
  on("tool.check", (_$, e) => {
    if (e.tool === "CronDelete" && options.deleteUnavailable) throw new Error("No such tool available: CronDelete");
    return { decision: options.decisions?.[e.tool] ?? "allow" };
  });
  on("tool.call", { tool: START }, () => {
    recorded.calls.push("start");
    return { result: START_TEXT };
  });
  on("tool.call", { tool: "CronList" }, () => {
    recorded.calls.push("CronList");
    if (options.listThrows) throw new Error("No such tool available: CronList");
    if (options.listError !== undefined) return { deny: options.listError };
    return { result: { jobs: jobs.map((job) => ({ cron: "*/30 * * * *", humanSchedule: "Every 30 minutes", ...job })) } };
  });
  on("tool.call", { tool: "CronCreate" }, (_$, e) => {
    recorded.calls.push("CronCreate");
    if (options.createError !== undefined) return { deny: options.createError };
    if (typeof e.cron !== "string" || typeof e.prompt !== "string" ||
        (e.recurring !== undefined && typeof e.recurring !== "boolean")) {
      throw new Error("CronCreateの試験入力が不正");
    }
    recorded.created.push({ cron: e.cron, prompt: e.prompt, recurring: e.recurring });
    const id = options.createdId ?? "cron-1";
    jobs.push({ id, prompt: e.prompt });
    return { result: { id, humanSchedule: "Every 30 minutes", recurring: true } };
  });
  on("tool.call", { tool: "CronDelete" }, (_$, e) => {
    recorded.calls.push(`CronDelete:${e.id}`);
    const index = jobs.findIndex((job) => job.id === e.id);
    if (index >= 0) jobs.splice(index, 1);
    return { result: { id: e.id } };
  });
  on("process.run", (_$, e) => {
    recorded.argv.push(e.argv);
    return {
      value: {
        exitCode: options.scheduleExitCode ?? 0,
        stdout:
          options.stdout ??
          `${JSON.stringify({ cron: options.cron ?? "*/30 * * * *", prompt: options.prompt ?? SCHEDULE_PROMPT })}\n`,
        stderr: options.scheduleExitCode ? "wait-schedule failed" : "",
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    };
  });
  return recorded;
}

const START_INPUT = { tool: START, cwd: "/repo", mode: "explore", prompt: "質問" } as const;

test("CronListの必要項目を検査し、不明なtaskを回収判断へ渡さない", () => {
  expect(parseCronJobs({ jobs: [{ id: "a", prompt: "p", durable: true }, { id: "b", prompt: "q" }] })).toEqual([
    { id: "a", prompt: "p", durable: true }, { id: "b", prompt: "q", durable: undefined },
  ]);
  for (const result of [null, {}, { jobs: null }, { jobs: [null] },
    { jobs: [{ id: 1, prompt: "p" }] }, { jobs: [{ id: "a", prompt: null }] },
    { jobs: [{ id: "a", prompt: "p", durable: "true" }] }]) {
    let rejected = false;
    try { parseCronJobs(result); } catch { rejected = true; }
    expect(rejected).toBe(true);
  }
});

test("main start creates one task from json: taskを持たないメインのstartはJSONのcronとpromptで1件作成し、task IDを文脈へ届ける", async ($, on) => {
  const recorded = engine(on, { createdId: "cron-main" });
  const result = await $.tool.call(START_INPUT);
  expect(result.deny).toBeUndefined();
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate"]);
  expect(recorded.created).toEqual([{ cron: "*/30 * * * *", prompt: SCHEDULE_PROMPT, recurring: true }]);
  const argv = recorded.argv[0] ?? [];
  expect(argv.slice(-5)).toEqual(["wait-schedule", "--request-bucket", "main", "--format", "json"]);
  expect(result.context?.length).toBe(1);
  expect(result.context?.[0]).toContain("task ID: cron-main");
  expect(result.context?.[0]).toContain('kind="notice"');
  expect(result.context?.[0]?.endsWith("\n</atk-auto>")).toBe(true);
});

test("同じ実行主体の続くstartはtaskを増やさず通知もしない", async ($, on) => {
  const recorded = engine(on, {});
  await $.tool.call(START_INPUT);
  const second = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate", "start", "CronList"]);
  expect(second.context ?? []).toEqual([]);
});

test("標識付きのtaskを既に持つメインは作成も通知もしない", async ($, on) => {
  const recorded = engine(on, { jobs: [{ id: "existing", prompt: `${PERIODIC_RECHECK_MARKER}\n確認する。` }] });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList"]);
  expect(recorded.argv).toEqual([]);
  expect(result.context ?? []).toEqual([]);
});

test("標識の無いtaskだけならメインは作成する", async ($, on) => {
  const recorded = engine(on, { jobs: [{ id: "other", prompt: "別の定期処理" }] });
  await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate"]);
});

test("CronCreateの許可の判定がdenyなら作成せず、許可確認の案内を届ける", async ($, on) => {
  const recorded = engine(on, { decisions: { CronCreate: "deny" } });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList"]);
  expect(result.context?.[0]).toContain('kind="warn"');
  expect(result.context?.[0]).toContain("`CronCreate`の許可の判定が`deny`");
  expect(result.context?.[0]).toContain("完了通知と終端観測で続ける");
});

test("CronListの許可の判定がdenyなら一覧も作成も呼ばずに案内を届ける", async ($, on) => {
  const recorded = engine(on, { decisions: { CronList: "deny" } });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start"]);
  expect(result.context?.[0]).toContain('kind="warn"');
});

test("CronCreateが拒否されたら理由と案内を届ける", async ($, on) => {
  const recorded = engine(on, { createError: "classifier denied" });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate"]);
  expect(result.context?.[0]).toContain("classifier denied");
  expect(result.context?.[0]).toContain("必要な許可と公開状態を確認する");
});

test("cron式を得られなければCronCreateを呼ばずに案内を届ける", async ($, on) => {
  const recorded = engine(on, { scheduleExitCode: 1, cron: "" });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList"]);
  expect(result.context?.[0]).toContain("cron式と標識付きのpromptを返さなかった");
  expect(result.context?.[0]).toContain("wait-schedule failed");
  expect(result.context?.[0]?.startsWith('<atk-auto source="periodic-recheck" kind="warn">\n')).toBe(true);
  expect(result.context?.[0]?.endsWith("\n</atk-auto>")).toBe(true);
});

test("終了境界内に義務を追加したpromptも標識で識別し、そのままCronCreateへ渡す", async ($, on) => {
  const prompt = `${PERIODIC_RECHECK_MARKER}\n定期再確認\n測定コマンド: date +%s; 判定閾値: 3600秒\n</atk-auto>`;
  const recorded = engine(on, { prompt });
  await $.tool.call(START_INPUT);
  expect(recorded.created[0]?.prompt).toBe(prompt);
});

test("JSONでない出力ではCronCreateを呼ばずに案内を届ける", async ($, on) => {
  const recorded = engine(on, { stdout: "*/30 * * * *\n" });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList"]);
  expect(result.context?.[0]).toContain('kind="warn"');
});

test("標識で始まらないpromptではCronCreateを呼ばずに案内を届ける", async ($, on) => {
  const recorded = engine(on, { prompt: "標識の無い本文" });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList"]);
  expect(result.context?.[0]).toContain('kind="warn"');
});

test("CronCreateが公開されていなければ案内を届ける", async ($, on) => {
  const recorded: string[] = [];
  on("session.messages", () => ({ value: [] }));
  on("tool.check", () => ({ decision: "allow" }));
  on("tool.call", { tool: START }, () => {
    recorded.push("start");
    return { result: START_TEXT };
  });
  on("tool.call", { tool: "CronList" }, () => ({ result: { jobs: [] } }));
  on("tool.call", { tool: "CronCreate" }, () => {
    throw new Error("No such tool available: CronCreate");
  });
  on("process.run", () => ({
    value: { exitCode: 0, stdout: "*/3 * * * *\n", stderr: "", isStdoutTruncated: false, isStderrTruncated: false },
  }));
  const result = await $.tool.call(START_INPUT);
  expect(recorded).toEqual(["start"]);
  expect(result.context?.[0]).toContain('kind="warn"');
});

test("startが拒否された呼び出しでは装着しない", async ($, on) => {
  const calls: string[] = [];
  on("tool.call", { tool: START }, () => ({ deny: "入力の誤り" }));
  on("tool.call", { tool: "CronList" }, () => {
    calls.push("CronList");
    return { result: { jobs: [] } };
  });
  await $.tool.call(START_INPUT);
  expect(calls).toEqual([]);
});

test("他のツールの呼び出しには関与しない", async ($, on) => {
  const calls: string[] = [];
  // 応答言語の判定が本文を取得できると、判定を経ずに後続へ渡る。
  on("session.messages", () => ({ value: [] }));
  on("tool.call", { tool: "mcp__plugin_agent-toolkit_agents_server__list" }, () => ({ result: "[]" }));
  on("tool.call", { tool: "CronList" }, () => {
    calls.push("CronList");
    return { result: { jobs: [] } };
  });
  const result = await $.tool.call({ tool: "mcp__plugin_agent-toolkit_agents_server__list" });
  expect(calls).toEqual([]);
  expect(result.context ?? []).toEqual([]);
});

test("subagent start does not mount: サブエージェントのstartは一覧も作成も通知もしない", async ($, on) => {
  const recorded = engine(on, {});
  // テストのエンジンは`$.tool.call`の入力の`agentId`をイベントへ渡すため、サブエージェントの呼び出しをこれで再現する。
  const result = await $.tool.call({ ...START_INPUT, agentId: "agent-7" } as typeof START_INPUT);
  expect(recorded.calls).toEqual(["start"]);
  expect(recorded.argv).toEqual([]);
  expect(result.context ?? []).toEqual([]);
});

for (const decision of ["ask", "deny"] as const) {
  test(`modの削除能力が${decision}ならtaskを作成せず理由を届ける`, async ($, on) => {
    const recorded = engine(on, { decisions: { CronDelete: decision } });
    const result = await $.tool.call(START_INPUT);
    expect(recorded.created).toEqual([]);
    expect(result.context?.[0]).toContain("CronDelete");
  });
}

test("複数startから一部待機・全結果回収までmodがtaskの寿命を管理する", async ($, on) => {
  const recorded = engine(on, {});
  await $.tool.call(START_INPUT);
  await $.tool.call(START_INPUT);
  const path = "/home/tester/.claude/agent-toolkit-function-hooks/recheck-life-1.txt";
  const turn = { answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" as const };
  recorded.files.set(path, "pending");
  await $.turn.complete(turn);
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate", "start", "CronList"]);
  recorded.files.set(path, "complete");
  await $.turn.complete(turn);
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate", "start", "CronList", "CronList", "CronDelete:cron-1"]);
  expect(recorded.created.length).toBe(1);
});

test("再読込後の既存共通taskもID台帳なしで通常回収する", async ($, on) => {
  const recorded = engine(on, { jobs: [{ id: "resumed", prompt: SCHEDULE_PROMPT }] });
  await $.tool.call(START_INPUT);
  recorded.files.set("/home/tester/.claude/agent-toolkit-function-hooks/recheck-life-1.txt", "complete");
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" });
  expect(recorded.created).toEqual([]);
  expect(recorded.calls).toEqual(["start", "CronList", "CronList", "CronDelete:resumed"]);
});

test("中断した前ターンの終了判定は新ターンで無効になる", async ($, on) => {
  const recorded = engine(on, { jobs: [{ id: "resumed", prompt: SCHEDULE_PROMPT }] });
  const path = "/home/tester/.claude/agent-toolkit-function-hooks/recheck-life-1.txt";
  recorded.files.set(path, "complete");
  await $.turn.start({ text: "再開", turnId: "next" });
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "next", reason: "answer" });
  expect(recorded.files.get(path)).toBe("consumed");
  expect(recorded.calls).toEqual([]);
});

test("modでCronDeleteの能力確認が例外になる環境ではtaskを作成しない", async ($, on) => {
  const recorded = engine(on, { deleteUnavailable: true });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.created).toEqual([]);
  expect(result.context?.[0]).toContain("CronDelete");
});

// 同じ会話へ同じ失敗を重ねて届けると、成功した`start`ごとに同じ警告が会話へ積み上がる。
// 通知だけを省き装着の評価は毎回行うことで、許可の回復後の自動作成を保つ。
function warnings(context: readonly string[] | undefined): string[] {
  return (context ?? []).filter((note) => note.includes('kind="warn"'));
}

test("同じ理由の装着失敗は同じ会話で初回だけ届け、startの結果と装着の評価は毎回保つ", async ($, on) => {
  const recorded = engine(on, { decisions: { CronCreate: "ask" } });
  const results = [];
  for (let index = 0; index < 3; index++) results.push(await $.tool.call(START_INPUT));
  expect(results.map((result) => warnings(result.context).length)).toEqual([1, 0, 0]);
  expect(results.map((result) => result.result)).toEqual([START_TEXT, START_TEXT, START_TEXT]);
  expect(recorded.calls).toEqual(["start", "CronList", "start", "CronList", "start", "CronList"]);
  expect(recorded.created).toEqual([]);
});

// 自動装着の失敗分岐ごとに、同じ失敗の反復を抑え、理由が変わった時点で新しい理由を届けることを確かめる。
const FAILURES: [string, Options, string][] = [
  ["CronListの許可判定", { decisions: { CronList: "ask" } }, "`CronList`の許可の判定が`ask`"],
  ["CronListの拒否", { listError: "一覧の拒否" }, "一覧の拒否"],
  ["wait-scheduleの失敗", { scheduleExitCode: 1 }, "cron式と標識付きのpromptを返さなかった"],
  ["CronDeleteの許可判定", { decisions: { CronDelete: "ask" } }, "`CronDelete`の許可の判定が`ask`"],
  ["CronDeleteの能力確認の例外", { deleteUnavailable: true }, "`CronDelete`の能力確認が失敗した"],
  ["CronCreateの許可判定", { decisions: { CronCreate: "deny" } }, "`CronCreate`の許可の判定が`deny`"],
  ["CronCreateの拒否", { createError: "作成の拒否" }, "作成の拒否"],
  ["装着の例外", { listThrows: true }, "装着の処理が例外で終わった"],
];

for (const [name, failure, reason] of FAILURES) {
  test(`${name}の反復は通知せず、理由が変わると新しい理由を届ける`, async ($, on) => {
    const options: Options = { ...failure };
    engine(on, options);
    const first = warnings((await $.tool.call(START_INPUT)).context);
    expect(first.length).toBe(1);
    expect(first[0]).toContain(reason);
    expect(warnings((await $.tool.call(START_INPUT)).context)).toEqual([]);
    // 失敗の条件を外し、別の分岐の失敗へ切り替える。
    for (const key of Object.keys(failure) as (keyof Options)[]) delete options[key];
    const changed = name === "CronCreateの許可判定" ? "ask" : "deny";
    options.decisions = { CronCreate: changed };
    const next = warnings((await $.tool.call(START_INPUT)).context);
    expect(next.length).toBe(1);
    expect(next[0]).toContain(`\`CronCreate\`の許可の判定が\`${changed}\``);
  });
}

test("許可の回復後は自動作成し、既存taskを再利用し、正常化後の新しい失敗は再び届ける", async ($, on) => {
  const options: Options = { decisions: { CronCreate: "ask" } };
  const recorded = engine(on, options);
  expect(warnings((await $.tool.call(START_INPUT)).context).length).toBe(1);
  expect(warnings((await $.tool.call(START_INPUT)).context)).toEqual([]);
  options.decisions = {};
  const mounted = await $.tool.call(START_INPUT);
  expect(mounted.context?.length).toBe(1);
  expect(mounted.context?.[0]).toContain("task ID: cron-1");
  expect((await $.tool.call(START_INPUT)).context ?? []).toEqual([]);
  expect(recorded.created.length).toBe(1);
  // taskを失った後に作成の許可を再び失うと、正常化の後に生じた失敗として届ける。
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" });
  recorded.files.set("/home/tester/.claude/agent-toolkit-function-hooks/recheck-life-1.txt", "complete");
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "t1", reason: "answer" });
  expect(recorded.calls).toContain("CronDelete:cron-1");
  options.decisions = { CronCreate: "ask" };
  const failedAgain = warnings((await $.tool.call(START_INPUT)).context);
  expect(failedAgain.length).toBe(1);
  expect(failedAgain[0]).toContain("`CronCreate`の許可の判定が`ask`");
});

test("別の会話では同じ失敗の初回通知を届ける", async ($, on) => {
  const options: Options = { decisions: { CronCreate: "ask" } };
  engine(on, options);
  expect(warnings((await $.tool.call(START_INPUT)).context).length).toBe(1);
  expect(warnings((await $.tool.call(START_INPUT)).context)).toEqual([]);
  options.sessionId = "life-2";
  expect(warnings((await $.tool.call(START_INPUT)).context).length).toBe(1);
});
