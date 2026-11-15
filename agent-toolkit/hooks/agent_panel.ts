import type { EngineInterface, On, SessionUsage } from "claude-code";
import { automatedMessage } from "./automated_message.ts";
import { pluginLaunchCommand } from "./plugin_launch.ts";

const SERVER = "plugin_agent-toolkit_agents_server";
type Member = { session_id: string; status: string; effort?: string };
type Permissions = { pending: { request: { request_id: string; scope: string } }[]; approvals: unknown[]; results: unknown[]; complete: boolean };
type Cost = { estimated_usd: number | null; known_usd: number; unknown_responses: number; incomplete: boolean };
type Panel = {
  open: boolean; tab: "agents" | "usage"; selfId: string; effortTarget: string;
  members?: Member[]; membersError?: string; selected: string;
  memberDetail?: Record<string, unknown>; memberError?: string; workerCost?: Cost; workerCostError?: string;
  cost?: Cost; costError?: string; costDetail: string; costExpanded: boolean;
  breakdown?: Record<string, Cost>; pricingAsOf?: string; usage?: SessionUsage; usageError?: string;
  permissions?: Permissions; permissionsError?: string; permissionsExpanded: boolean;
  permissionId: string; adoptId: string; permissionDetail: string; scope: string;
  deliveryDetail: string; deliveryExpanded: boolean;
  note: string; busy: boolean;
};

function costOf(input: unknown): Cost {
  const value = objectOf(input);
  if (!(value.estimated_usd === null || typeof value.estimated_usd === "number" && Number.isFinite(value.estimated_usd)) || typeof value.known_usd !== "number" || !Number.isFinite(value.known_usd) || typeof value.unknown_responses !== "number" || !Number.isInteger(value.unknown_responses) || value.unknown_responses < 0 || typeof value.incomplete !== "boolean") throw new Error("費用応答の形式が不正");
  return { estimated_usd: value.estimated_usd, known_usd: value.known_usd, unknown_responses: value.unknown_responses, incomplete: value.incomplete };
}

function costSummary(cost: Cost): string {
  return `推定合計: ${cost.estimated_usd === null ? "未確定" : `${cost.estimated_usd} USD`} / 既知額: ${cost.known_usd} USD\n単価不明: ${cost.unknown_responses}応答 / 原本不足: ${cost.incomplete ? "あり" : "なし"}`;
}

function objectOf(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("応答がオブジェクトではない");
  return value as Record<string, unknown>;
}

function failure(label: string, error: unknown): string {
  return `${label}に失敗: ${error instanceof Error ? error.message : String(error)}`;
}

async function call($: EngineInterface, tool: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const result = await $.mcp.call(SERVER, tool, args);
  if (result.isError) throw new Error("MCPがエラー応答を返した。接続と入力を確認せよ。");
  return objectOf(JSON.parse(result.content.filter((block) => block.type === "text").map((block) => block.text).join("\n")));
}

async function refreshMembers($: EngineInterface, state: Panel): Promise<void> {
  try {
    const value = await call($, "list", { include_terminated: true });
    if (!Array.isArray(value.sessions) || !value.sessions.every((item) => item && typeof item === "object" && typeof item.session_id === "string" && typeof item.status === "string")) throw new Error("担当一覧の形式が不正");
    state.members = value.sessions;
    const previous = state.selected;
    state.selected = state.members.some((item) => item.session_id === state.selected) ? state.selected : state.members[0]?.session_id ?? "";
    if (previous !== state.selected) { state.memberDetail = undefined; state.workerCost = undefined; state.memberError = undefined; state.workerCostError = undefined; }
    if (!state.effortTarget || state.effortTarget !== state.selfId && !state.members.some((item) => item.session_id === state.effortTarget)) state.effortTarget = state.selected || state.selfId;
    state.membersError = undefined;
  } catch (error) {
    state.members = undefined;
    state.selected = "";
    state.effortTarget = state.selfId;
    state.membersError = failure("担当一覧の取得", error);
  }
}

async function refreshCost($: EngineInterface, state: Panel): Promise<void> {
  try {
    const result = await $.process.run(pluginLaunchCommand($.plugin.root, "agent_toolkit/_agents_server/ui_panel.py", ["--session-id", await $.session.id()]), { timeoutMs: 30_000 });
    if (result.isStdoutTruncated || result.isStderrTruncated) throw new Error("費用応答が切り詰められた");
    if (result.exitCode !== 0) throw new Error(`終了コード${result.exitCode}。${result.stderr.trim().split(/\r?\n/).at(-1) ?? "診断なし"}`);
    const value = objectOf(JSON.parse(result.stdout));
    state.cost = costOf(value);
    const breakdown = value.breakdown === undefined ? undefined : objectOf(value.breakdown);
    state.breakdown = breakdown ? Object.fromEntries(["self", "claude_subagents", "delegates"].map((key) => [key, costOf(breakdown[key])])) : undefined;
    const pricing = value.pricing === undefined ? undefined : objectOf(value.pricing);
    state.pricingAsOf = typeof pricing?.as_of === "string" ? pricing.as_of : undefined;
    state.costDetail = JSON.stringify(value, null, 2);
    state.costError = undefined;
  } catch (error) {
    state.cost = undefined;
    state.breakdown = undefined;
    state.pricingAsOf = undefined;
    state.costDetail = "";
    state.costExpanded = false;
    state.costError = failure("費用の取得", error);
  }
}

async function refreshUsage($: EngineInterface, state: Panel): Promise<void> {
  try { state.usage = await $.session.usage(); state.usageError = undefined; }
  catch (error) { state.usage = undefined; state.usageError = failure("利用枠・contextの取得", error); }
}

async function refreshSelected($: EngineInterface, state: Panel): Promise<void> {
  if (!state.selected) { state.memberDetail = undefined; state.workerCost = undefined; state.memberError = undefined; state.workerCostError = undefined; return; }
  try { state.memberDetail = await call($, "show", { session_id: state.selected, verbose: true }); state.memberError = undefined; }
  catch (error) { state.memberDetail = undefined; state.memberError = failure("担当詳細の取得", error); }
  try {
    const result = await $.process.run(pluginLaunchCommand($.plugin.root, "agent_toolkit/_agents_server/ui_panel.py", ["--session-id", state.selected, "--self-only"]), { timeoutMs: 30_000 });
    if (result.isStdoutTruncated || result.isStderrTruncated) throw new Error("担当費用応答が切り詰められた");
    if (result.exitCode !== 0) throw new Error(`終了コード${result.exitCode}。${result.stderr.trim().split(/\r?\n/).at(-1) ?? "診断なし"}`);
    // 選択担当自身は既存算出のinclude_descendants=Falseで取得し、全体へ加算しない。
    const value = objectOf(JSON.parse(result.stdout));
    state.workerCost = costOf(value);
    state.workerCostError = undefined;
  } catch (error) { state.workerCost = undefined; state.workerCostError = failure("担当自身の費用取得", error); }
}

async function refreshAll($: EngineInterface, state: Panel): Promise<void> {
  await Promise.all([refreshMembers($, state), refreshCost($, state), refreshPermissions($, state), refreshUsage($, state)]);
  await refreshSelected($, state);
}

async function refreshPermissions($: EngineInterface, state: Panel): Promise<void> {
  try {
    const value = await call($, "permissions", { action: "list" });
    if (!Array.isArray(value.pending) || !value.pending.every((item) => item && typeof item === "object" && typeof item.request?.request_id === "string" && typeof item.request?.scope === "string") || !Array.isArray(value.approvals) || !Array.isArray(value.results) || typeof value.complete !== "boolean") throw new Error("権限一覧の形式が不正");
    state.permissions = { pending: value.pending, approvals: value.approvals, results: value.results, complete: value.complete };
    state.permissionId = state.permissions.pending.some((item) => item.request.request_id === state.permissionId) ? state.permissionId : state.permissions.pending[0]?.request.request_id ?? "";
    state.permissionsError = undefined;
  } catch (error) {
    state.permissions = undefined;
    state.permissionId = "";
    state.permissionsError = failure("権限一覧の取得", error);
  }
}

export function register(on: On): void {
  const state: Panel = { open: false, tab: "agents", selfId: "", effortTarget: "", selected: "", costDetail: "", costExpanded: false, permissionsExpanded: false, permissionId: "", adoptId: "", permissionDetail: "", scope: "once", deliveryDetail: "", deliveryExpanded: false, note: "", busy: false };
  on("command.run", { command: "atk" }, async ($) => {
    try {
      const opened = await $.ui.open({ id: "atk-agents", title: "担当操作", focus: true, closeOnEscape: true });
      if (!opened.isPlaced) { state.open = false; return { text: `対話画面にパネルを配置できない。${opened.reason}` }; }
    }
    catch (error) { return { text: failure("パネルの表示", error) }; }
    state.open = true;
    if (state.busy) return {};
    state.busy = true;
    $.ui.invalidate("ui.render");
    try {
      try { state.selfId = await $.session.id(); } catch (error) { state.note = failure("自身の識別", error); }
      await refreshAll($, state);
    }
    finally { state.busy = false; $.ui.invalidate("ui.render"); }
    return {};
  });
  on("ui.close", { id: "atk-agents" }, async ($, e, next) => { const result = await next(e); state.open = false; return result; });
  on("turn.complete", { turnId: /[\s\S]/ }, async ($, e, next) => {
    const result = await next(e);
    if (!state.open || state.busy || e.agentId !== undefined) return result;
    state.busy = true;
    try { await refreshAll($, state); }
    finally { state.busy = false; $.ui.invalidate("ui.render"); }
    return result;
  });
  on("ui.render", { component: "Pane" }, ($, e, next) => {
    if (e.requestId !== "atk-agents") return next(e);
    const { Box, Text, Button, Select, Input } = $.ui.resolve(e);
    const summary = state.cost
      ? costSummary(state.cost)
      : state.costError ?? "費用は未取得。";
    const detail = state.memberDetail;
    const apiError = detail?.api_error && typeof detail.api_error === "object" && !Array.isArray(detail.api_error) ? objectOf(detail.api_error) : undefined;
    const active = Array.isArray(detail?.active_tool_uses) ? detail.active_tool_uses.at(-1) : undefined;
    const action = detail?.progress || (active && typeof active === "object" ? active.type ?? active.tool_name : undefined) || "未取得";
    const waitReason = apiError ? `${String(apiError.type ?? "API待機")} / 解除: ${String(apiError.resets_at ?? "未取得")}` : detail?.result_held === true ? "子・背景タスクの完了待ち" : "待機の申告なし";
    return Box({ flexDirection: "column", children: [
      Text({ children: [summary] }),
      Box({ flexDirection: "row", columnGap: 1, children: [
        Button({ key: "tab-agents", label: "担当", onPress: () => { state.tab = "agents"; $.ui.invalidate("ui.render"); } }),
        Button({ key: "tab-usage", label: "使用量", onPress: () => { state.tab = "usage"; $.ui.invalidate("ui.render"); } }),
      ] }),
      Box({ flexDirection: "row", columnGap: 1, children: [
        Button({ key: "cost-detail", label: state.costExpanded ? "費用詳細を閉じる" : "費用詳細を展開", onPress: () => { state.costExpanded = !state.costExpanded; $.ui.invalidate("ui.render"); } }),
        Button({ key: "member-refresh", label: "担当一覧を更新", onPress: async () => {
          if (state.busy) return;
          state.busy = true;
          try { await refreshMembers($, state); await refreshSelected($, state); }
          finally { state.busy = false; $.ui.invalidate("ui.render"); }
        } }),
      ] }),
      ...(state.tab === "agents" ? [
      ...(state.members?.length ? [Select({ key: "member", value: state.selected, options: state.members.map((member) => ({ value: member.session_id, label: `${member.session_id} ${member.status} ${member.effort ?? ""}` })), onSelect: async (value) => {
        if (state.busy) return;
        state.selected = value; state.effortTarget = value; state.busy = true;
        try { await refreshSelected($, state); } finally { state.busy = false; $.ui.invalidate("ui.render"); }
      } })] : [Text({ children: [state.membersError ?? (state.members ? "担当はいない。" : "担当一覧は未取得。")] })]),
      Text({ children: [detail ? `担当: ${String(detail.label || state.selected)} / 状態: ${String(detail.status ?? "未取得")}\nモデル: ${String(detail.model ?? "未取得")} / effort: ${String(detail.effort ?? "未取得")}\n直近動作: ${String(action)}\n待機理由: ${waitReason}` : state.memberError ?? "担当詳細は未取得。"] }),
      Input({ key: "message", label: "追送", value: "", onSubmit: async (prompt) => {
        if (state.busy || !state.selected || !prompt.trim()) return;
        state.busy = true;
        state.note = "追送中…";
        $.ui.invalidate("ui.render");
        try {
          const result = await call($, "send_message", { session_id: state.selected, prompt });
          // previous_resultを含む既存の配送応答は保持するが、主会話を勝手に再開するpromptは送らない。
          state.deliveryDetail = JSON.stringify(result, null, 2);
          state.deliveryExpanded = false;
          if (result.delivery === "steered" || result.delivery === "reply_started") {
            state.note = `配送済み（${result.delivery}）。`;
            await refreshSelected($, state); await refreshMembers($, state);
          } else state.note = result.delivery === "reply_ambiguous" ? "配送成否は未確認。再送せず担当状態を確認せよ。" : "追送に失敗。担当状態を確認せよ。";
        }
        catch (error) { state.note = failure("追送", error); }
        finally { state.busy = false; $.ui.invalidate("ui.render"); }
      } }),
      ...(state.deliveryDetail ? [Button({ key: "delivery-detail", label: "配送結果を展開", onPress: () => { state.deliveryExpanded = !state.deliveryExpanded; $.ui.invalidate("ui.render"); } })] : []),
      ...((state.selfId || state.members?.length) ? [Select({ key: "effort-target", value: state.effortTarget, options: [...(state.selfId ? [{ value: state.selfId, label: "自身" }] : []), ...(state.members ?? []).map((member) => ({ value: member.session_id, label: `${member.session_id} / effort: ${member.effort ?? "未取得"}` }))], onSelect: (value) => { state.effortTarget = value; $.ui.invalidate("ui.render"); } })] : []),
      Box({ flexDirection: "row", columnGap: 1, children: (["up", "down"] as const).map((direction) => Button({ key: direction, label: direction === "up" ? "effortを1段上げる" : "effortを1段下げる", onPress: async () => {
        if (state.busy || !state.effortTarget) return;
        state.busy = true;
        state.note = "effort変更中…";
        $.ui.invalidate("ui.render");
        const target = state.effortTarget;
        try {
          // modの子プロセスにはBash用の会話IDが付かないため、実会話APIのIDをCLIの本人確認へ渡す。
          const result = await $.process.run(pluginLaunchCommand($.plugin.root, "agent_toolkit/atk.py", ["agents", "effort", direction, target]), { timeoutMs: 35_000, env: { CLAUDE_CODE_SESSION_ID: await $.session.id() } });
          if (result.isStdoutTruncated || result.isStderrTruncated) throw new Error("effort応答が切り詰められた。再送せず現在値を確認せよ。");
          let value: Record<string, unknown>;
          try { value = objectOf(JSON.parse(result.stdout)); }
          catch { throw new Error(`終了コード${result.exitCode}。${result.stderr.trim() || result.stdout.trim() || "応答本文がない"}`); }
          if (value.error) throw new Error(String(value.error));
          if (result.exitCode !== 0) throw new Error(`終了コード${result.exitCode}。${result.stderr.trim() || result.stdout}`);
          state.note = typeof value.effort === "string" ? `effort: ${value.effort} / 対象: ${target}（${value.at_boundary === true ? "上限・下限に到達、変更なし" : "次のturnから適用"}）` : "effort変更の応答は受け取ったが、現在値は未確認。";
          await refreshSelected($, state); await refreshMembers($, state);
        } catch (error) { state.note = failure("effort変更", error); }
        finally { state.busy = false; $.ui.invalidate("ui.render"); }
      } })) }),
      Text({ children: [state.busy ? state.note.endsWith("中…") ? state.note : "取得・操作中。" : state.note || "操作待ち。"] }),
      Text({ children: [state.permissions ? `権限: 保留${state.permissions.pending.length}件 / 承認${state.permissions.approvals.length}件 / 応答${state.permissions.results.length}件${state.permissions.complete ? "" : "（一部未取得）"}` : state.permissionsError ?? "権限一覧は未取得。"] }),
      Box({ flexDirection: "row", columnGap: 1, children: [
        Button({ key: "permission-refresh", label: "権限状態を更新", onPress: async () => {
          if (state.busy) return;
          state.busy = true;
          try { await refreshPermissions($, state); state.permissionDetail = ""; }
          finally { state.busy = false; $.ui.invalidate("ui.render"); }
        } }),
        Button({ key: "permission-list-detail", label: "権限一覧の詳細", onPress: () => { state.permissionsExpanded = !state.permissionsExpanded; $.ui.invalidate("ui.render"); } }),
      ] }),
      ...(state.permissions?.pending.length ? [Select({ key: "permission", value: state.permissionId, options: state.permissions.pending.map((item) => ({ value: item.request.request_id, label: `${item.request.request_id} ${item.request.scope}` })), onSelect: (value) => { state.permissionId = value; state.permissionDetail = ""; $.ui.invalidate("ui.render"); } })] : []),
      Input({ key: "permission-scope", label: "試行範囲（once / count:N）", value: state.scope, onInput: (value) => { state.scope = value; }, onSubmit: (value) => { state.scope = value; } }),
      Button({ key: "permission-prepare", label: "この実入力の確認を求める", onPress: async () => {
        if (state.busy || !state.permissionId) return;
        state.busy = true;
        try {
          const prepared = await call($, "permissions", { action: "prepare", request_id: state.permissionId, scope: state.scope });
          if (typeof prepared.display !== "string") throw new Error("確認要求の表示本文がない");
          const request = objectOf(prepared.request);
          if (typeof request.request_id !== "string") throw new Error("新しい要求IDがない");
          const newId = request.request_id;
          if (state.permissions) state.permissions.pending = state.permissions.pending.map((item) => item.request.request_id === state.permissionId ? { ...item, request: { ...item.request, request_id: newId, scope: state.scope } } : item);
          state.permissionId = newId;
          state.permissionDetail = prepared.display;
          await $.prompt.submit({ text: automatedMessage("permissions-panel", "notice", "次の要求JSON全体を質問本文に逐語で含め、既存AskUserQuestionでユーザーへ確認せよ。allow_choiceの選択肢と拒否の選択肢を提示し、回答を代筆せず実回答を待て。コード全体・実入力・出所・scopeを省略するな。\n" + prepared.display) });
          state.note = "確認を依頼した。承認や実行の成功は記録していない。";
        } catch (error) { state.note = failure("確認の依頼", error); }
        finally { state.busy = false; $.ui.invalidate("ui.render"); }
      } }),
      Box({ flexDirection: "row", columnGap: 1, children: [
        Button({ key: "permission-detail", label: "選択した要求の全文を展開", onPress: async () => {
          if (state.busy || !state.permissionId) return;
          state.busy = true;
          try { state.permissionDetail = JSON.stringify(await call($, "permissions", { action: "get", request_id: state.permissionId }), null, 2); }
          catch (error) { state.note = failure("要求全文の取得", error); }
          finally { state.busy = false; $.ui.invalidate("ui.render"); }
        } }),
        Button({ key: "permission-revoke", label: "取消", onPress: async () => {
          if (state.busy || !state.permissionId) return;
          state.busy = true;
          try { const result = await call($, "permissions", { action: "revoke", request_id: state.permissionId }); state.note = result.changed === true ? "取消を記録した。" : "取消による変更は確認できない。"; }
          catch (error) { state.note = failure("取消", error); }
          finally { state.busy = false; $.ui.invalidate("ui.render"); }
        } }),
      ] }),
      Input({ key: "permission-id", label: "子から引き継ぐ要求ID", value: state.adoptId, onSubmit: (value) => { state.adoptId = value; }, onInput: (value) => { state.adoptId = value; } }),
      Button({ key: "permission-adopt", label: "選択した担当の要求を引き継ぐ", onPress: async () => {
        if (state.busy || !state.selected || !state.adoptId) return;
        state.busy = true;
        try { await call($, "permissions", { action: "adopt", source_session: state.selected, request_id: state.adoptId }); state.note = "要求を引き継いだ。権限状態を更新して確認せよ。"; }
        catch (error) { state.note = failure("要求の引継ぎ", error); }
        finally { state.busy = false; $.ui.invalidate("ui.render"); }
      } }),
      ] : [
        Button({ key: "usage-refresh", label: "使用量・費用を更新", onPress: async () => {
          if (state.busy) return;
          state.busy = true;
          try { await Promise.all([refreshCost($, state), refreshUsage($, state), refreshSelected($, state)]); }
          finally { state.busy = false; $.ui.invalidate("ui.render"); }
        } }),
        Text({ children: [state.usage ? `context: ${state.usage.context.tokens ?? "未取得"} / ${state.usage.context.window} tokens（${state.usage.context.percent ?? "未取得"}%）\n${state.usage.rateLimits.length ? state.usage.rateLimits.map((limit) => `${limit.kind}: ${limit.percentUsed}% / 解除: ${limit.resetsAt ?? "未取得"}`).join("\n") : "利用枠: APIからの申告なし"}` : state.usageError ?? "利用枠・contextは未取得。"] }),
        Text({ children: [`料金表基準日: ${state.pricingAsOf ?? "未取得"}\n${state.breakdown ? Object.entries(state.breakdown).map(([key, cost]) => `${key === "self" ? "自己" : key === "claude_subagents" ? "組込み子の追加分" : "委譲の追加分"}: ${costSummary(cost)}`).join("\n") : "費用内訳は未取得。"}`] }),
        Text({ children: [state.workerCost ? `選択担当 ${state.selected} 自身（全体へ再加算しない）\n${costSummary(state.workerCost)}` : state.workerCostError ?? "選択担当自身の費用は未取得。"] }),
      ]),
      Button({ key: "close", label: "閉じる", onPress: async () => {
        try { await $.ui.close({ id: "atk-agents" }); state.open = false; }
        catch (error) { state.note = failure("パネルを閉じる操作", error); $.ui.invalidate("ui.render"); }
      } }),
      ...(state.costExpanded && state.costDetail ? [Text({ children: [state.costDetail] })] : []),
      ...(state.permissionsExpanded && state.permissions ? [Text({ children: [JSON.stringify(state.permissions, null, 2)] })] : []),
      ...(state.permissionDetail ? [Text({ children: [state.permissionDetail] })] : []),
      ...(state.deliveryExpanded && state.deliveryDetail ? [Text({ children: [state.deliveryDetail] })] : []),
    ] });
  });
}
