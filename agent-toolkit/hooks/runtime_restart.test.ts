import { expect, mock, test } from "claude-code/testing";

for (const scenario of ["restart", "update", "process", "none", "other", "repl", "failure", "delegated", "plugin", "path", "ownership", "denied", "exception"] as const) {
  test(`再起動は所有waitだけを止めJSON印とホスト拒否を保つ: ${scenario}`, async ($, on) => {
    mock.env(on, { HOME: "/home/tester", ...(scenario === "process" ? { AGENT_TOOLKIT_PROCESS_LOOP_SESSION: "1" } : {}), ...(scenario === "delegated" ? { AGENT_TOOLKIT_DELEGATED_SESSION: "1" } : {}) });
    const clock = mock.clock(on);
    const withWait = !["process", "none"].includes(scenario);
    const saved: string[] = [];
    const stopped: string[] = [];
    let core = 0;
    on("session.id", () => ({ value: scenario === "path" ? "invalid/id" : "controller" }));
    on("session.start", (_$, e) => ({ cwd: e.cwd }));
    on("command.register", (_$, e) => ({ value: { command: e.name } }));
    on("command.run", () => { core++; return ["other", "repl"].includes(scenario) ? { text: "Can't restart while work is running in the background" } : {}; });
    on("fs.list", () => ({ value: withWait ? [{ name: `ui-request-controller-${"a".repeat(32)}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false }] : [] }));
    on("fs.exists", () => ({ value: true }));
    on("fs.read", (_$, e) => ({ value: e.path.includes("ui-request-") ? JSON.stringify({ id: "a".repeat(32), operation: scenario === "repl" ? "repl_bash_started" : "background_task", input: { id: "owned-wait", command: scenario === "other" ? "sleep 600" : "atk agents wait", ...(scenario === "repl" ? { expires: 99999999999 } : {}) }, expires: 99999999999 }) : "consumed" }));
    on("fs.write", (_$, e) => { if (e.path.endsWith("/restart-controller.txt")) saved.push(e.text); return { value: undefined }; });
    on("process.run", () => {
      if (scenario === "exception") throw new Error("fixture preparation failure");
      return { value: { exitCode: scenario === "ownership" ? 1 : 0, stdout: scenario === "other" ? '{"eligible":true,"reason":"一般背景Bash"}' : '{"eligible":true,"sessions":[{"session_id":"child"}]}', stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("tool.check", () => ({ decision: scenario === "denied" ? "deny" : "allow" }));
    on("tool.call", { tool: "TaskStop" }, (_$, e) => {
      stopped.push(String(e.task_id));
      return scenario === "failure" ? { isError: true, result: "停止失敗", text: "停止失敗" } : { result: { task_id: String(e.task_id), task_type: "local_bash", message: "Successfully stopped task" } };
    });
    on("turn.start", (_$, e) => ({ turnId: e.turnId }));
    await $.session.start({ cwd: "/work", surface: null, isInteractive: true });
    await $.turn.start({ text: "作業", turnId: "controller-turn" });
    await clock.advance(100);
    if (scenario === "plugin") {
      const input = { command: "restart", args: "", origin: { kind: "plugin", name: "other-plugin" }, presentation: { isFullscreen: false, columns: 80 } } as const;
      await $.command.run(input);
    } else {
      const result = await $.command.run({ command: scenario === "update" ? "update" : "restart", args: "", origin: { kind: "composer" }, presentation: { isFullscreen: false, columns: 80 } });
      if (["path", "ownership", "denied", "exception"].includes(scenario)) {
        expect(result.text).toContain(scenario === "path" || scenario === "exception" ? "プラグイン管理者へ報告せよ" : "/tasks");
        expect(core).toBe(0);
        expect(saved).toEqual([]);
        expect(stopped).toEqual([]);
        return;
      }
    }
    expect(core).toBe(scenario === "failure" ? 0 : 1);
    if (["delegated", "plugin", "none", "other", "repl"].includes(scenario)) {
      expect(saved).toEqual([]);
      expect(stopped).toEqual([]);
    } else if (scenario === "failure") expect(saved).toEqual([]);
    else {
      expect(JSON.parse(saved.at(-1) ?? "{}")).toEqual({ phase: "pending", waits: scenario === "process" ? [] : [{ id: "owned-wait", command: "atk agents wait" }] });
    }
  });
}

for (const status of ["completed", "failed", "killed"] as const) {
  test(`背景タスクの終了通知は該当IDだけを除き、再起動では残った所有waitだけを止める: ${status}`, async ($, on) => {
    mock.env(on, { HOME: "/home/tester" });
    mock.session(on);
    const clock = mock.clock(on);
    const requests = { ["a".repeat(32)]: "finished-wait", ["b".repeat(32)]: "remaining-wait" };
    const registered: string[][] = [];
    const stopped: string[] = [];
    on("session.id", () => ({ value: "controller" }));
    on("session.start", (_$, e) => ({ cwd: e.cwd }));
    on("command.register", (_$, e) => ({ value: { command: e.name } }));
    on("command.run", () => ({ text: "再起動を受理した" }));
    on("fs.list", () => ({ value: Object.keys(requests).map((id) => ({ name: `ui-request-controller-${id}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false })) }));
    on("fs.exists", () => ({ value: true }));
    on("fs.read", (_$, e) => {
      const request = Object.entries(requests).find(([id]) => e.path.endsWith(`ui-request-controller-${id}.txt`));
      if (!request) return { value: "consumed" };
      const [id, taskId] = request;
      return { value: JSON.stringify({ id, operation: "background_task", input: { id: taskId, command: "atk agents wait" }, expires: 99999999999 }) };
    });
    on("fs.write", () => ({ value: undefined }));
    on("state.set", { plugin: "agent-toolkit", key: "backgroundWaits" }, async (_$, e, next) => {
      const result = await next(e);
      registered.push(Object.keys(e.value ?? {}).sort());
      return result;
    });
    on("process.run", () => ({ value: { exitCode: 0, stdout: '{"eligible":true,"sessions":[{"session_id":"child"}]}', stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
    on("tool.check", () => ({ decision: "allow" }));
    on("tool.call", { tool: "TaskStop" }, (_$, e) => {
      stopped.push(String(e.task_id));
      return { result: { task_id: String(e.task_id), task_type: "local_bash", message: "Successfully stopped task" } };
    });
    on("turn.start", (_$, e) => ({ turnId: e.turnId }));
    await $.session.start({ cwd: "/work", surface: null, isInteractive: true });
    await $.turn.start({ text: "作業", turnId: "controller-turn" });
    await clock.advance(100);
    expect(registered.at(-1)).toEqual(["finished-wait", "remaining-wait"]);

    await $.session.append({
      door: "delivery", origin: { kind: "composer" }, uuid: "5a19a2af-394f-4123-8c40-72e5e53e2d34",
      message: { type: "user", content: [{ type: "text", text: `<task-notification><task-id>finished-wait</task-id><status>${status}</status></task-notification>` }] },
    });
    expect(registered.at(-1)).toEqual(["remaining-wait"]);

    const result = await $.command.run({ command: "restart", args: "", origin: { kind: "composer" }, presentation: { isFullscreen: false, columns: 80 } });
    expect(result.text).toBe("再起動を受理した");
    expect(stopped).toEqual(["remaining-wait"]);
  });
}
