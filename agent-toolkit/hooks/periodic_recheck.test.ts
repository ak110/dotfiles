import type { On } from "claude-code";
import { expect, test } from "claude-code/testing";

import { PERIODIC_RECHECK_MARKER, PERIODIC_RECHECK_PROMPT } from "./periodic_recheck_prompt.ts";

// `agents_server`の`start`の後に、呼出主体の定期再確認のtaskを1件だけ作成し、結果を会話へ届けることを検証する。
// 装着しないと、モデルが起動前の手順を省いた場合に停滞と外部ジョブの終了を検出する主体が欠ける。

const START = "mcp__plugin_agent-toolkit_agents_server__start" as const;
const START_TEXT = '{"session_id": "s1", "status": "running"}';

type Job = { id: string; prompt: string };

type Options = {
  jobs?: Job[];
  decisions?: Record<string, "allow" | "ask" | "deny">;
  cron?: string;
  scheduleExitCode?: number;
  createError?: string;
  createdId?: string;
};

type Recorded = { calls: string[]; argv: (readonly string[])[]; created: { cron: string; prompt: string; recurring?: boolean }[] };

function engine(on: On, options: Options): Recorded {
  const recorded: Recorded = { calls: [], argv: [], created: [] };
  const jobs = [...(options.jobs ?? [])];
  on("tool.check", (_$, e) => ({ decision: options.decisions?.[e.tool] ?? "allow" }));
  on("tool.call", { tool: START }, () => {
    recorded.calls.push("start");
    return { result: START_TEXT };
  });
  on("tool.call", { tool: "CronList" }, () => {
    recorded.calls.push("CronList");
    return { result: { jobs: jobs.map((job) => ({ cron: "*/30 * * * *", humanSchedule: "Every 30 minutes", ...job })) } };
  });
  on("tool.call", { tool: "CronCreate" }, (_$, e) => {
    recorded.calls.push("CronCreate");
    if (options.createError !== undefined) return { deny: options.createError };
    recorded.created.push({ cron: e.cron, prompt: e.prompt, recurring: e.recurring });
    const id = options.createdId ?? "cron-1";
    jobs.push({ id, prompt: e.prompt });
    return { result: { id, humanSchedule: "Every 30 minutes", recurring: true } };
  });
  on("process.run", (_$, e) => {
    recorded.argv.push(e.argv);
    return {
      value: {
        exitCode: options.scheduleExitCode ?? 0,
        stdout: `${options.cron ?? "*/30 * * * *"}\n`,
        stderr: options.scheduleExitCode ? "wait-schedule failed" : "",
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    };
  });
  return recorded;
}

const START_INPUT = { tool: START, cwd: "/repo", mode: "explore", prompt: "質問" } as const;

test("taskを持たないメインのstartは定期再確認のtaskを1件作成し、task IDを文脈へ届ける", async ($, on) => {
  const recorded = engine(on, { createdId: "cron-main" });
  const result = await $.tool.call(START_INPUT);
  expect(result.deny).toBeUndefined();
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate"]);
  expect(recorded.created).toEqual([{ cron: "*/30 * * * *", prompt: PERIODIC_RECHECK_PROMPT, recurring: true }]);
  expect(recorded.created[0]?.prompt.split("\n")[0]).toBe(PERIODIC_RECHECK_MARKER);
  const argv = recorded.argv[0] ?? [];
  expect(argv.slice(-3)).toEqual(["wait-schedule", "--request-bucket", "main"]);
  expect(result.context?.length).toBe(1);
  expect(result.context?.[0]).toContain("task ID: cron-main");
  expect(result.context?.[0]).toContain('kind="notice"');
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

test("CronCreateの許可の判定がdenyなら作成せず、モデルが装着する案内を届ける", async ($, on) => {
  const recorded = engine(on, { decisions: { CronCreate: "deny" } });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList"]);
  expect(result.context?.[0]).toContain('kind="warn"');
  expect(result.context?.[0]).toContain("`CronCreate`の許可の判定が`deny`");
  expect(result.context?.[0]).toContain("「Cronによる定期再確認」に従って自ら装着する");
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
  expect(result.context?.[0]).toContain("自ら装着する");
});

test("cron式を得られなければCronCreateを呼ばずに案内を届ける", async ($, on) => {
  const recorded = engine(on, { scheduleExitCode: 1, cron: "" });
  const result = await $.tool.call(START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList"]);
  expect(result.context?.[0]).toContain("cron式を返さなかった");
  expect(result.context?.[0]).toContain("wait-schedule failed");
});

test("CronCreateが公開されていなければ案内を届ける", async ($, on) => {
  const recorded: string[] = [];
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
  on("tool.call", { tool: "mcp__plugin_agent-toolkit_agents_server__list" }, () => ({ result: "[]" }));
  on("tool.call", { tool: "CronList" }, () => {
    calls.push("CronList");
    return { result: { jobs: [] } };
  });
  const result = await $.tool.call({ tool: "mcp__plugin_agent-toolkit_agents_server__list" });
  expect(calls).toEqual([]);
  expect(result.context ?? []).toEqual([]);
});

test("サブエージェントのstartはsubagentのbucketで作成する", async ($, on) => {
  const recorded = engine(on, { createdId: "cron-sub" });
  // テストのエンジンは`$.tool.call`の入力の`agentId`をイベントへ渡すため、サブエージェントの呼び出しをこれで再現する。
  const result = await $.tool.call({ ...START_INPUT, agentId: "agent-7" } as typeof START_INPUT);
  const argv = recorded.argv[0] ?? [];
  expect(argv.slice(-3)).toEqual(["wait-schedule", "--request-bucket", "subagent"]);
  expect(result.context?.[0]).toContain("request bucket: subagent");
});

test("メインの標識付きtaskはサブエージェントの保有に数えない", async ($, on) => {
  const recorded = engine(on, { jobs: [{ id: "main-task", prompt: `${PERIODIC_RECHECK_MARKER}\n確認する。` }] });
  const result = await $.tool.call({ ...START_INPUT, agentId: "agent-8" } as typeof START_INPUT);
  expect(recorded.calls).toEqual(["start", "CronList", "CronCreate"]);
  expect(result.context?.[0]).toContain("request bucket: subagent");
});
