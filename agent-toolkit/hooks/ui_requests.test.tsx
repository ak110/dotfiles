import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";

function queue(on: On, session: string, id: string) {
  on("fs.list", () => ({ value: [{ name: `ui-request-${session}-${id}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false }] }));
  on("fs.exists", () => ({ value: true }));
}

for (const interactive of [false, true]) {
  test(`対話画面の確認はsurfacesの実取得を返す: ${interactive}`, async ($, on) => {
    mock.env(on, { HOME: "/home/tester" });
    const clock = mock.clock(on);
    const id = "1".repeat(32);
    queue(on, "interactive-main", id);
    on("session.id", () => ({ value: "interactive-main" }));
    on("turn.start", (_$, e) => ({ turnId: e.turnId }));
    on("session.surfaces", () => ({ value: interactive ? ["terminal"] : [] }));
    on("session.version", () => ({ value: { version: "2.1.325" } }));
    on("settings.read", (_$, e) => ({ value: e.source === "flag" ? {} : { autoMode: { soft_deny: ["Soft: requires consent"], hard_deny: ["Hard: blocked"] } } }));
    on("fs.read", () => ({ value: JSON.stringify({ id, operation: "interactive_available", input: {}, expires: 99999999999 }) }));
    const replies: string[] = [];
    on("fs.write", (_$, e) => { replies.push(e.text); return { value: undefined }; });
    await $.turn.start({ text: "画面確認", turnId: "interactive-turn" });
    await clock.advance(100);
    const response = JSON.parse(replies.at(-1) ?? "{}");
    expect(response.ok).toBe(true);
    expect(response.interactive).toBe(interactive);
    expect(response.version).toBe("2.1.325");
    expect(response.flag_auto_mode_present).toBe(false);
    expect(response.auto_mode).toEqual({ soft_deny: ["Soft: requires consent"], hard_deny: ["Hard: blocked"] });
  });
}

for (const placed of [true, false]) {
  test(`MCP報告は配置確認${placed}を応答し同じ要求を二重表示しない`, async ($, on) => {
    mock.env(on, { HOME: "/home/tester" });
    const clock = mock.clock(on);
    const id = "a".repeat(32);
    queue(on, "ui-session", id);
    let placements = 0;
    const responses: string[] = [];
    on("session.id", () => ({ value: "ui-session" }));
    on("turn.start", (_$, e) => ({ turnId: e.turnId }));
    on("fs.read", () => ({ value: JSON.stringify({ id, operation: "send_to_user", input: { message: "表示する本文" }, expires: 99999999999 }) }));
    on("fs.write", (_$, e) => { responses.push(e.text); return { value: undefined }; });
    on("ui.place", () => {
      placements++;
      return { value: placed ? { isPlaced: true, requestId: "placed-row" } : { isPlaced: false, reason: "対応画面なし" } };
    });
    await $.turn.start({ text: "操作", turnId: "ui-turn" });
    await clock.advance(100);
    await clock.advance(100);
    expect(placements).toBe(1);
    const response = JSON.parse(responses.at(-1) ?? "{}");
    expect(response.id).toBe(id);
    expect(response.ok).toBe(placed);
  });
}

test("MCP圧縮要求を表示要求と分けて予約受付する", async ($, on) => {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  const id = "b".repeat(32);
  const responses: string[] = [];
  queue(on, "compact-ui-session", id);
  on("session.id", () => ({ value: "compact-ui-session" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("fs.read", () => ({ value: JSON.stringify({ id, operation: "compact_conversation", input: {}, expires: 99999999999 }) }));
  on("fs.write", (_$, e) => { responses.push(e.text); return { value: undefined }; });
  on("ui.place", () => { throw new Error("圧縮予約で画面へ配置しない"); });
  await $.turn.start({ text: "圧縮", turnId: "compact-ui-turn" });
  await clock.advance(100);
  const result = JSON.parse(responses.at(-1) ?? "{}");
  expect(result.id).toBe(id);
  expect(result.ok).toBe(true);
  expect(result.reason).toContain("完了ではない");
});

test("反映予約はターン後に公開コマンドを一度実行し結果を通知する", async ($, on) => {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  queue(on, "runtime-ui-session", "c".repeat(32));
  const commands: string[] = [];
  const prompts: string[] = [];
  on("session.id", () => ({ value: "runtime-ui-session" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("turn.complete", () => ({ text: "" }));
  on("fs.read", () => ({ value: JSON.stringify({ id: "c".repeat(32), operation: "apply_runtime_changes", input: { targets: ["plugins", "agents_server", "pyfltr"] }, expires: 99999999999 }) }));
  on("fs.write", () => ({ value: undefined }));
  on("command.run", (_$, e) => { commands.push(`${e.command} ${e.args}`.trim()); return { text: "反映コマンドの応答", context: ["確認用の文脈"] }; });
  on("prompt.submit", (_$, e) => { prompts.push(e.text); return { text: "" }; });
  await $.turn.start({ text: "反映", turnId: "runtime-ui-turn" });
  await clock.advance(100);
  expect(commands).toEqual([]);
  const completed = { answer: "", durationMs: 0, isAborted: false, turnId: "runtime-ui-turn", reason: "answer" as const };
  await $.turn.complete(completed);
  await clock.advance(0);
  await $.turn.complete(completed);
  await clock.advance(0);
  expect(commands).toEqual(["mcp reconnect plugin:agent-toolkit:agents_server", "mcp reconnect plugin:agent-toolkit:pyfltr", "reload-plugins"]);
  expect(prompts.length).toBe(1);
  expect(prompts[0]).toContain("確認用の文脈");
});

for (const expired of [false, true]) {
test(`Stop一覧から独立してREPL待機の期限を守る: ${expired}`, async ($, on) => {
  mock.env(on, { HOME: "/home/tester" });
  let polling = true;
  on("clock.every", { ms: 100 }, async (_$, e) => {
    if (!polling) return { deny: "要求を取り込んだ後の反復は焦点試験の対象外" };
    await clock.sleep(e.ms);
    return { value: undefined };
  });
  const clock = mock.clock(on);
  let compactions = 0;
  const ids = ["d".repeat(32), "e".repeat(32)];
  on("fs.list", () => ({ value: ids.map((id) => ({ name: `ui-request-expiry-main-${id}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false })) }));
  on("fs.exists", () => ({ value: true }));
  on("session.id", () => ({ value: "expiry-main" }));
  on("session.start", (_$, e) => ({ cwd: e.cwd }));
  on("command.register", (_$, e) => ({ value: { command: e.name } }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("turn.complete", () => ({ text: "" }));
  on("session.usage", () => ({ value: { startedAt: 0, context: { tokens: 500_000, window: 1_000_000 }, rateLimits: [] } }));
  on("session.surfaces", () => ({ value: ["terminal"] }));
  on("ui.log", () => ({ value: undefined }));
  on("session.compact", () => { compactions++; return { messages: [{ role: "user", text: "保存した作業状態", toolUses: [] }] }; });
  on("process.run", () => ({ value: { exitCode: 0, stdout: '{"eligible":true}', stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  on("fs.read", (_$, e) => {
    if (!e.path.includes("ui-request-")) return { value: "consumed" };
    const id = ids.find((id) => e.path.includes(id)) ?? "";
    return { value: JSON.stringify({ id, operation: id === ids[1] ? "background_snapshot" : "repl_bash_started",
      input: id === ids[1] ? { ids: [] } : { id: "same-repl-id", command: "atk agents wait", expires: (Date.now() + (expired ? 5000 : 90_000)) / 1000 },
      created: id === ids[1] ? "2" : "1", expires: 99999999999 }) };
  });
  on("fs.write", () => ({ value: undefined }));
  await $.session.start({ cwd: "/work", surface: null, isInteractive: true });
  await $.turn.start({ text: "復帰", turnId: "expiry-turn" });
  await clock.advance(200);
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "expiry-turn", reason: "answer" });
  polling = false;
  await clock.advance(60_000);
  expect(compactions).toBe(expired ? 0 : 1);
});
}
