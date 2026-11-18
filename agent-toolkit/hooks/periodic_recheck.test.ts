import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";
import { parseCronJobs, parseSchedule, PERIODIC_RECHECK_MARKER } from "./periodic_recheck.ts";

test("一覧と共通promptの不正を回収判断へ渡さない", () => {
  expect(parseSchedule("broken")).toBeUndefined();
  expect(parseSchedule(JSON.stringify({ cron: "* * * * *", prompt: "無関係" }))).toBeUndefined();
  expect(parseCronJobs({ jobs: [{ id: "durable", prompt: "保持", durable: true }] })[0]?.durable).toBe(true);
});

// 設定hookが`start`の成功を観測して置く装着要求を、modの要求処理（`ui_requests.tsx`）に読ませる。`waiting`が偽ならhookは応答の待機を諦めている。
function startRequests(on: On, denied: string, options: { waiting?: () => boolean } = {}) {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  const state = { created: 0, requestId: "e".repeat(32) };
  const responses: { id: string; ok: boolean; reason: string }[] = [];
  const prompts: string[] = [];
  on("session.id", () => ({ value: "recheck-main" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("fs.write", (_$, e) => {
    if (e.path.includes("/ui-response-")) responses.push(JSON.parse(e.text));
    return { value: undefined };
  });
  on("fs.list", () => ({ value: [{ name: `ui-request-recheck-main-${state.requestId}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false }] }));
  on("fs.read", () => ({ value: JSON.stringify({ id: state.requestId, operation: "periodic_recheck", input: {}, expires: 99999999999 }) }));
  on("fs.exists", (_$, e) => ({ value: !e.path.includes("/ui-request-") || (options.waiting?.() ?? true) }));
  on("tool.check", (_$, e) => ({ decision: e.tool === denied ? "deny" : "allow" }));
  on("tool.call", { tool: "CronList" }, () => ({ result: { jobs: state.created ? [{ id: "cron", prompt: PERIODIC_RECHECK_MARKER + "\n待機" }] : [] } }));
  on("tool.call", { tool: "CronCreate" }, () => { state.created++; return { result: { id: "cron" } }; });
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ cron: "* * * * *", prompt: PERIODIC_RECHECK_MARKER + "\n待機" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  return { clock, state, responses, prompts };
}

for (const denied of ["none", "CronList", "CronCreate", "CronDelete"]) {
  test(`startの装着要求へ、作成の成否を同じ要求の応答で返す: ${denied}`, async ($, on) => {
    const { clock, state, responses, prompts } = startRequests(on, denied);
    await $.turn.start({ text: "起動後", turnId: "recheck-turn" });
    await clock.advance(100);
    expect(state.created).toBe(denied === "none" ? 1 : 0);
    expect(responses.at(-1)?.ok).toBe(true);
    if (denied === "none") expect(responses.at(-1)?.reason).toBe("");
    else expect(responses.at(-1)?.reason).toContain(`\`${denied}\``);
    state.requestId = "f".repeat(32);
    await clock.advance(100);
    expect(state.created).toBe(denied === "none" ? 1 : 0);
    expect(responses.length).toBe(2);
    // 既にtaskを持つ場合と、同じ失敗が続く場合は何も添えない。
    expect(responses.at(-1)?.reason).toBe("");
    expect(prompts).toEqual([]);
  });
}

for (const denied of ["none", "CronCreate"]) {
  test(`待機を諦めた要求の装着失敗だけを次のターンへ届ける: ${denied}`, async ($, on) => {
    const { clock, responses, prompts } = startRequests(on, denied, { waiting: () => false });
    await $.turn.start({ text: "起動後", turnId: "recheck-turn" });
    await clock.advance(100);
    expect(responses).toEqual([]);
    if (denied === "none") expect(prompts).toEqual([]);
    else {
      expect(prompts.length).toBe(1);
      expect(prompts[0]).toContain("`CronCreate`");
    }
  });
}
