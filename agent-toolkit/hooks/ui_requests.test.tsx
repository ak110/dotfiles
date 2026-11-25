import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";
import outputPolicy from "../agent_toolkit/_common/output-policy.ts";
import { placedMessage } from "./ui_requests.tsx";

function assertOutputPolicy(profile: keyof typeof outputPolicy.acceptance, text: string) {
  const check = outputPolicy.acceptance[profile];
  expect(text.length).toBeLessThan(check.max_chars + 1);
  for (const word of check.includes) expect(text).toContain(word);
  for (const word of check.excludes) expect(text).not.toContain(word);
}

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
    if (placed) {
      expect(response.reason.split("\n")[0]).toBe("送信済み。再送・再掲・確認・要約は不要。新しい報告はsend_to_userで送る。新情報がなく本文が必須なら「…」。");
      expect(response.reason).toContain("新しい報告はsend_to_userで送る");
      const rendered = JSON.stringify(await $.ui.render({ surface: "terminal", component: "Placed", requestId: "placed-row", props: { plugin: "agent-toolkit", id } }));
      expect(rendered).toContain('"text":"表示する本文"');
      expect(rendered).not.toContain("送達時刻");
      assertOutputPolicy("send_first", response.reason);
    } else {
      expect(response.reason).toContain("対応画面なし");
      expect(response.reason).toContain("未送信");
      expect(response.reason).toContain("通常の応答本文で伝えて");
      expect(response.reason).not.toContain("送信済み");
    }
  });
}

test("MCP送信済みの詳しい案内は初回だけ返し、新しい警告は残す", async ($, on) => {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  let id = "d".repeat(32);
  const responses: string[] = [];
  on("fs.list", () => ({ value: [{ name: `ui-request-guide-${id}.txt`, kind: "file", size: 1, mtimeMs: 0, isLink: false }] }));
  on("fs.exists", () => ({ value: true }));
  on("session.id", () => ({ value: "guide" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("fs.read", () => ({ value: JSON.stringify({ id, operation: "send_to_user", input: { message: "終了時刻は15:2xです。" }, expires: 99999999999 }) }));
  on("fs.write", (_$, e) => { if (e.path.includes("/ui-response-")) responses.push(JSON.parse(e.text).reason); return { value: undefined }; });
  on("ui.place", () => ({ value: { isPlaced: true, requestId: "placed-row" } }));
  await $.turn.start({ text: "操作", turnId: "guide-turn" });
  await clock.advance(100);
  id = "e".repeat(32);
  await clock.advance(100);
  expect(responses.length).toBe(2);
  expect(responses[0]).toContain("新しい報告はsend_to_userで送る");
  expect(responses[1]).not.toContain("新しい報告はsend_to_userで送る");
  assertOutputPolicy("send_repeat", responses[1]?.split("\n")[0] ?? "");
  expect(responses[1]).toContain("時刻の一部が伏せ字");
  expect((responses[1] ?? "").length).toBeLessThan((responses[0] ?? "").length);
});

test("bugfixの記録に再発防止の行が無いMCP報告は配置せずに差し戻す", async ($, on) => {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  const id = "c".repeat(32);
  queue(on, "ui-session", id);
  let placements = 0;
  const responses: string[] = [];
  const message = "期待: 一覧が表示される。\n原因: 設定の読み違い。\n処置: 直した。\n確認: 再実行した。";
  on("session.id", () => ({ value: "ui-session" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("fs.read", () => ({ value: JSON.stringify({ id, operation: "send_to_user", input: { message }, expires: 99999999999 }) }));
  on("fs.write", (_$, e) => { responses.push(e.text); return { value: undefined }; });
  on("ui.place", () => { placements++; return { value: { isPlaced: true, requestId: "placed-row" } }; });
  await $.turn.start({ text: "操作", turnId: "ui-turn" });
  await clock.advance(100);
  await clock.advance(100);
  expect(placements).toBe(0);
  const response = JSON.parse(responses.at(-1) ?? "{}");
  expect(response.ok).toBe(false);
  expect(response.reason).toContain("未送信");
  expect(response.reason).toContain("`再発防止:`");
  expect(response.resend).toBe(true);
  expect(response.reason).not.toContain("送信済み");
});

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
  let observed: string[] = [];
  on("state.set", { plugin: "agent-toolkit", key: "backgroundWaits" }, async (_$, e, next) => {
    const result = await next(e);
    observed = Object.keys(e.value ?? {});
    return result;
  });
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
  expect(observed).toEqual(["same-repl-id"]);
  await clock.advance(60_000);
  expect(observed).toEqual(expired ? [] : ["same-repl-id"]);
  expect(compactions).toBe(0);
});
}

const SEND_TO_USER = "mcp__plugin_agent-toolkit_agents_server__send_to_user";

function call(message: string) {
  return { tool_use_id: `use-${message}`, tool: SEND_TO_USER, input: { message }, isRunning: false, isErrored: false, isInterrupted: false, output: "ok" };
}

for (const status of ["isErrored", "isRunning", "isInterrupted"] as const) {
test(`${status}の送信本文を既定の描画へ渡さず、診断と許可後の本文は保つ`, async ($, on) => {
  const body = "送り直す本文";
  const denied = { ...call(body), [status]: true, output: "Permission denied" };
  const retry = { ...call(body), tool_use_id: "retry" };
  const inputs: unknown[] = [];
  on("ui.render", { component: "ToolUse" }, (_$, e, next) => {
    inputs.push([e.props.input, e.props.output]);
    return next(e);
  });
  on("ui.render", { component: "ToolGroup" }, (_$, e, next) => {
    inputs.push(e.props.calls.map(({ input }) => input));
    return next(e);
  });
  await $.ui.render({ surface: "terminal", component: "ToolUse", requestId: "denied", props: denied });
  await $.ui.render({ surface: "terminal", component: "ToolGroup", requestId: "mixed", props: { calls: [denied, retry], isActive: false, isExpanded: false } });
  expect(inputs).toEqual([[{}, "Permission denied"], [{}, { message: body }]]);
  const allowed = await $.ui.render({ surface: "terminal", component: "ToolUse", requestId: "retry", props: retry });
  expect(JSON.stringify(allowed)).toContain('"Markdown","props":{"text":"送り直す本文"}');
});
}

test("会話の再開後は置いた行が無い送達本文をツール入力から描き、稼働中に置いた本文は二重に描かない", async ($, on) => {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  const id = "b".repeat(32);
  queue(on, "resume-session", id);
  on("session.id", () => ({ value: "resume-session" }));
  on("turn.start", (_$, e) => ({ turnId: e.turnId }));
  on("fs.read", () => ({ value: JSON.stringify({ id, operation: "send_to_user", input: { message: "稼働中に置いた本文", tool_use_id: "use-live" }, expires: 99999999999 }) }));
  on("fs.write", () => ({ value: undefined }));
  on("ui.place", () => ({ value: { isPlaced: true, requestId: "placed-row" } }));
  const resumed = await $.ui.render({ surface: "terminal", component: "ToolUse", requestId: "use-resumed", props: call("再開後に読む本文") });
  expect(JSON.stringify(resumed)).toContain('"Markdown","props":{"text":"再開後に読む本文"}');
  // 稼働中の送達で本文を置いた後は、同じ本文のツール行を描き替えない。
  await $.turn.start({ text: "操作", turnId: "resume-turn" });
  await clock.advance(100);
  const live = await $.ui.render({ surface: "terminal", component: "ToolUse", requestId: "use-live", props: { ...call("稼働中に置いた本文"), tool_use_id: "use-live" } });
  expect(JSON.stringify(live)).not.toContain('"Markdown"');
  // 再開前に同じ本文を送った別の呼出しは、稼働中の送達の後も元の位置に描く。
  const earlier = await $.ui.render({ surface: "terminal", component: "ToolUse", requestId: "use-earlier", props: { ...call("稼働中に置いた本文"), tool_use_id: "use-earlier" } });
  expect(JSON.stringify(earlier)).toContain('"Markdown","props":{"text":"稼働中に置いた本文"}');
});

test("再開後にまとめられた送達は展開して呼び出しごとに描く", async ($, on) => {
  const expanded: boolean[] = [];
  on("ui.render", { component: "ToolGroup" }, (_$, e, next) => {
    expanded.push(e.props.isExpanded);
    return next(e);
  });
  await $.ui.render({ surface: "terminal", component: "ToolGroup", requestId: "group", props: { calls: [call("一つ目"), call("二つ目")], isActive: false, isExpanded: false } });
  expect(expanded).toEqual([true]);
});

for (const [label, stored, text] of [
  ["新形式", "本文", "本文"],
  ["旧形式", { message: "旧版の本文", placedAt: 10 }, "旧版の本文"],
] as const) {
  test(`配置済みの行は${label}の保存状態から本文だけを描き、送達時刻を描かない`, async ($, on) => {
    const id = "d".repeat(32);
    on("state.get", () => ({ value: { value: { [id]: stored }, version: 1 } }));
    expect(placedMessage(stored)).toBe(text);
    const rendered = JSON.stringify(await $.ui.render({ surface: "terminal", component: "Placed", requestId: "stored-row", props: { plugin: "agent-toolkit", id } }));
    expect(rendered).toContain(`"text":"${text}"`);
    expect(rendered).not.toContain("送達時刻");
  });
}
