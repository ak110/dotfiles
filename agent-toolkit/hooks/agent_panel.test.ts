import type { On } from "claude-code";
import { type Engine, expect, mock, test } from "claude-code/testing";

const amount = { estimated_usd: null, known_usd: 0.49, unknown_responses: 4, incomplete: false };
const cost = { ...amount, breakdown: { self: amount, claude_subagents: { ...amount, known_usd: 0.1 }, delegates: { ...amount, known_usd: 0.2 } }, pricing: { as_of: "2026-11-11" }, detail_marker: "FULL-COST-DETAIL" };

async function panel($: Engine, on: On, initialFailure = false) {
  mock.env(on, { HOME: "/home/tester" });
  const controls = { initialFailure, listFailure: false, sendFailure: true, effortFailure: true, empty: false, sends: [] as unknown[], effortActors: [] as (string | undefined)[], lists: 0, shows: 0, costs: 0 };
  on("session.id", () => ({ value: "panel-main" }));
  on("ui.open", () => ({ value: { isPlaced: true } }));
  on("ui.close", () => ({ value: undefined }));
  on("session.usage", () => ({ value: { startedAt: 0, context: { tokens: 500, window: 1000, percent: 50 }, rateLimits: [{ kind: "five_hour", percentUsed: 80, resetsAt: "2026-11-15T23:00:00Z" }] } }));
  on("turn.complete", () => ({ text: "" }));
  on("mcp.call", (_$, e) => {
    if (e.tool === "list") {
      controls.lists++;
      if (controls.initialFailure || controls.listFailure) throw new Error("一覧接続エラー");
      return { value: { isError: false, content: [{ type: "text", text: JSON.stringify({ sessions: controls.empty ? [] : [{ session_id: "worker", status: "running", effort: "medium" }] }) }] } };
    }
    if (e.tool === "show") {
      controls.shows++;
      return { value: { isError: false, content: [{ type: "text", text: JSON.stringify({ label: "担当A", status: "running", model: "codex-model", effort: "medium", progress: "ファイルを確認中", api_error: { type: "usage_limit", resets_at: "2026-11-16T00:00:00Z" } }) }] } };
    }
    if (e.tool === "send_message") {
      controls.sends.push(e.args);
      if (controls.sendFailure) throw new Error("配送エラー");
      return { value: { isError: false, content: [{ type: "text", text: '{"delivery":"reply_started","previous_result":{"status":"completed","agent_message":"旧担当結果"}}' }] } };
    }
    if (controls.initialFailure) return { value: { isError: true, content: [{ type: "text", text: "権限取得エラー" }] } };
    return { value: { isError: false, content: [{ type: "text", text: '{"pending":[],"approvals":[],"results":[],"complete":true}' }] } };
  });
  on("process.run", (_$, e) => {
    if (e.argv.includes("effort")) {
      controls.effortActors.push(e.init?.env?.CLAUDE_CODE_SESSION_ID);
      if (controls.effortFailure) return { value: { exitCode: 1, stdout: '{"error":"CLIの所有確認に失敗"}', stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
      return { value: { exitCode: 0, stdout: '{"effort":"high","applies":"next_turn"}', stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    }
    controls.costs++;
    return { value: { exitCode: 0, stdout: controls.initialFailure ? "{" : JSON.stringify(e.argv.includes("--self-only") ? { ...cost, estimated_usd: 0.12, known_usd: 0.12, unknown_responses: 0 } : cost), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
  });
  await $.command.run({ command: "atk", args: "", origin: { kind: "composer" }, presentation: { isFullscreen: false, columns: 80 } });
  const ui = await $.ui.mount({ plugin: "agent-toolkit", surface: "terminal", component: "Pane", requestId: "atk-agents", props: { title: "担当操作", isFocused: true, bodyColumns: 80, placement: "inline", scroll: { offset: 0, bodyRows: 42 }, view: {} } });
  return { controls, ui };
}

test("費用は概要だけを先頭に出し全結果は明示展開する", async ($, on) => {
  const { ui } = await panel($, on);
  expect(await ui.find({ type: "Text", text: /推定合計: 未確定/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /FULL-COST-DETAIL/ })).toBeUndefined();
  expect(await ui.find({ key: "message" })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /担当A/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /usage_limit/ })).toBeDefined();
  await ui.press({ key: "cost-detail" });
  expect(await ui.find({ type: "Text", text: /FULL-COST-DETAIL/ })).toBeDefined();
  await ui.press({ key: "cost-detail" });
  expect(await ui.find({ type: "Text", text: /FULL-COST-DETAIL/ })).toBeUndefined();
  await ui.press({ key: "tab-usage" });
  expect(await ui.find({ type: "Text", text: /2026-11-15T23:00:00Z/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /context: 500/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /料金表基準日: 2026-11-11/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /自身（全体へ再加算しない）/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /推定合計: 0.12 USD/ })).toBeDefined();
  await ui.unmount();
});

test("取得失敗を空の一覧や費用ゼロと混同せず更新で回復する", async ($, on) => {
  const { ui, controls } = await panel($, on, true);
  expect(await ui.find({ type: "Text", text: /費用の取得に失敗/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /担当一覧の取得に失敗/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /権限一覧の取得に失敗/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /^担当はいない。$/ })).toBeUndefined();
  controls.initialFailure = false;
  controls.empty = true;
  await ui.press({ key: "member-refresh" });
  expect(await ui.find({ type: "Text", text: /^担当はいない。$/ })).toBeDefined();
  await ui.unmount();
});

test("配送・effort例外後もbusyを解除し入力本文だけを一度送る", async ($, on) => {
  const { ui, controls } = await panel($, on);
  await ui.input({ key: "message", text: "追加指示\n入力本文だけ" });
  expect(await ui.find({ type: "Text", text: /追送に失敗/ })).toBeDefined();
  controls.sendFailure = false;
  const before = controls.shows;
  await ui.input({ key: "message", text: "追加指示\n入力本文だけ" });
  expect(controls.sends).toEqual([{ session_id: "worker", prompt: "追加指示\n入力本文だけ" }, { session_id: "worker", prompt: "追加指示\n入力本文だけ" }]);
  expect(controls.shows > before).toBe(true);
  expect(await ui.find({ type: "Text", text: /配送済み/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /旧担当結果/ })).toBeUndefined();
  await ui.press({ key: "delivery-detail" });
  expect(await ui.find({ type: "Text", text: /旧担当結果/ })).toBeDefined();
  await ui.press({ key: "up" });
  expect(await ui.find({ type: "Text", text: /effort変更に失敗/ })).toBeDefined();
  expect(await ui.find({ type: "Text", text: /CLIの所有確認に失敗/ })).toBeDefined();
  controls.effortFailure = false;
  await ui.press({ key: "up" });
  expect(await ui.find({ type: "Text", text: /次のturnから適用/ })).toBeDefined();
  expect(controls.effortActors).toEqual(["panel-main", "panel-main"]);
  const lists = controls.lists;
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "panel-turn", reason: "answer" });
  expect(controls.lists > lists).toBe(true);
  await ui.press({ key: "close" });
  const closedLists = controls.lists;
  await $.turn.complete({ answer: "", durationMs: 0, isAborted: false, turnId: "closed-turn", reason: "answer" });
  expect(controls.lists).toBe(closedLists);
  await ui.unmount();
});
