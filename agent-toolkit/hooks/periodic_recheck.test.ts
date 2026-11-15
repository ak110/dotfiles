import { expect, mock, test } from "claude-code/testing";
import { parseCronJobs, parseSchedule, PERIODIC_RECHECK_MARKER } from "./periodic_recheck.ts";

test("一覧と共通promptの不正を回収判断へ渡さない", () => {
  expect(parseSchedule("broken")).toBeUndefined();
  expect(parseSchedule(JSON.stringify({ cron: "* * * * *", prompt: "無関係" }))).toBeUndefined();
  expect(parseCronJobs({ jobs: [{ id: "durable", prompt: "保持", durable: true }] })[0]?.durable).toBe(true);
});

for (const denied of ["none", "CronList", "CronCreate", "CronDelete"]) {
  test(`設定hookのstart成功通知から装着する: ${denied}`, async ($, on) => {
    mock.env(on, { HOME: "/home/tester" });
    const clock = mock.clock(on);
    let created = 0;
    let requestId = "e".repeat(32);
    on("session.id", () => ({ value: "recheck-main" }));
    on("turn.start", (_$, e) => ({ turnId: e.turnId }));
    on("fs.write", () => ({ value: undefined }));
    on("fs.list", () => ({ value: [{ name: `ui-request-recheck-main-${requestId}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false }] }));
    on("fs.read", () => ({ value: JSON.stringify({ id: requestId, operation: "periodic_recheck", input: {}, expires: 99999999999 }) }));
    on("fs.exists", () => ({ value: true }));
    on("tool.check", (_$, e) => ({ decision: e.tool === denied ? "deny" : "allow" }));
    on("tool.call", { tool: "CronList" }, () => ({ result: { jobs: created ? [{ id: "cron", prompt: PERIODIC_RECHECK_MARKER + "\n待機" }] : [] } }));
    on("tool.call", { tool: "CronCreate" }, () => { created++; return { result: { id: "cron" } }; });
    on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ cron: "* * * * *", prompt: PERIODIC_RECHECK_MARKER + "\n待機" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
    on("prompt.submit", () => ({ text: "" }));
    await $.turn.start({ text: "起動後", turnId: "recheck-turn" });
    await clock.advance(100);
    expect(created).toBe(denied === "none" ? 1 : 0);
    requestId = "f".repeat(32);
    await clock.advance(100);
    expect(created).toBe(denied === "none" ? 1 : 0);
  });
}
