import type { EngineInterface, On } from "claude-code";
import { collectCommonTasks, exitStatePath } from "./session_exit.ts";
import { deliverySucceeded, missingPreventionLine, missingPreventionRejection, newReportRoute, reportWarnings } from "./send_to_user.tsx";
import { pluginLaunchCommand } from "./plugin_launch.ts";
import { startNextTurn } from "./automated_message.ts";
import { mountPeriodicRecheck } from "./periodic_recheck.ts";

type Request = { id: string; operation: string; input: { message?: string; tool_use_id?: unknown; targets?: unknown; resume?: unknown; id?: unknown; command?: unknown; ids?: unknown; expires?: unknown }; expires: number; created?: string; detached?: boolean };
import type { BackgroundWait, RuntimeChange } from "./state.d.ts";
export type { BackgroundWait, RuntimeChange } from "./state.d.ts";

function expireReplWait($: EngineInterface, wait: BackgroundWait): void {
  if (wait.replExpiresAt === undefined) return;
  $.clock.after(Math.max(0, wait.replExpiresAt - Date.now()), async () => {
    const held = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
    if (held[wait.id]?.replExpiresAt !== wait.replExpiresAt) return;
    delete held[wait.id];
    await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, held);
  });
}

export function register(on: On, session: { backgroundChanged?: boolean }): void {
  let busy = false;
  let initialized = false;
  let cleanupFailed = false;
  on("session.start", { cwd: /^/ }, async ($, e, next) => {
    const result = await next(e);
    const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
    for (const wait of Object.values(waits)) expireReplWait($, wait);
    return result;
  });
  on("turn.start", { turnId: /^.+$/ }, async ($, e, next) => {
    if (initialized || e.agentId !== undefined) return next(e);
    initialized = true;
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const configured = await $.env.get("CLAUDE_CONFIG_DIR");
    const sessionId = await $.session.id();
    const sourceBase = exitStatePath(sessionId, configured, home, "ui-request");
    const responseBase = exitStatePath(sessionId, configured, home, "ui-response");
    if (sourceBase && responseBase) $.clock.every(100, async () => {
      if (busy) return;
      busy = true;
      try {
        const directory = sourceBase.slice(0, sourceBase.lastIndexOf("/"));
        if (!(await $.fs.exists(directory))) return;
        const prefix = `ui-request-${sessionId}-`;
        const entries = await $.fs.list(directory);
        let cleanupNeeded = entries.some((entry) => entry.name.startsWith(`ui-response-${sessionId}-`) && entry.mtimeMs + 120_000 < Date.now());
        const batch: { source: string; name: string; request: Request }[] = [];
        for (const entry of entries) {
        if (entry.kind !== "file" || entry.isLink || !entry.name.startsWith(prefix) || !/^[a-f0-9]{32}\.txt$/.test(entry.name.slice(prefix.length))) continue;
        const source = `${directory}/${entry.name}`;
        let request: Request;
        try { request = JSON.parse(await $.fs.read(source)) as Request; } catch { continue; }
        if (!request || typeof request !== "object" || Array.isArray(request)) continue;
        if (request?.created !== undefined && (typeof request.created !== "string" || !/^\d+$/.test(request.created))) continue;
        batch.push({ source, name: entry.name, request });
        }
        batch.sort((a, b) => BigInt(a.request.created ?? "0") < BigInt(b.request.created ?? "0") ? -1 : BigInt(a.request.created ?? "0") > BigInt(b.request.created ?? "0") ? 1 : 0);
        for (const { source, name, request } of batch) {
        if (typeof request?.expires === "number" && request.expires * 1000 <= Date.now()) cleanupNeeded = true;
        const consumed = (await $.state.get({ plugin: "agent-toolkit", key: "uiConsumed" })).value ?? {};
        if (!request || typeof request.id !== "string" || !/^[a-f0-9]{32}$/.test(request.id) ||
            typeof request.expires !== "number" || !Number.isFinite(request.expires) || request.expires * 1000 <= Date.now() ||
            !request.input || typeof request.input !== "object" || Array.isArray(request.input) ||
            request.id in consumed || name !== `${prefix}${request.id}.txt`) continue;
        await $.state.set({ plugin: "agent-toolkit", key: "uiConsumed" },
          { ...Object.fromEntries(Object.entries(consumed).filter(([, expiry]) => expiry * 1000 > Date.now())), [request.id]: request.expires });
        let ok = false;
        let interactive: boolean | undefined;
        let version: string | undefined;
        let autoMode: { hard_deny?: unknown; soft_deny?: unknown } | undefined;
        let flagAutoModePresent: boolean | undefined;
        let reason = "未対応の会話操作";
        if (request.operation === "send_to_user" && typeof request.input?.message === "string" && missingPreventionLine(request.input.message)) {
          reason = missingPreventionRejection;
        } else if (request.operation === "send_to_user" && typeof request.input?.message === "string") {
          const messages = (await $.state.get({ plugin: "agent-toolkit", key: "uiMessages" })).value ?? {};
          await $.state.set({ plugin: "agent-toolkit", key: "uiMessages" }, { ...messages, [request.id]: request.input.message });
          const placed = await $.ui.place({ id: request.id });
          ok = placed.isPlaced;
          reason = placed.isPlaced ? "" : `${placed.reason}。報告は未送信です。必要な情報は通常の応答本文で伝えてください。`;
          if (ok) {
            reason = [deliverySucceeded, newReportRoute, ...reportWarnings(request.input.message)].join("\n");
          }
          if (!ok) await $.state.set({ plugin: "agent-toolkit", key: "uiMessages" }, messages);
          else if (typeof request.input.tool_use_id === "string") {
            const calls = (await $.state.get({ plugin: "agent-toolkit", key: "uiCalls" })).value ?? {};
            await $.state.set({ plugin: "agent-toolkit", key: "uiCalls" }, { ...calls, [request.input.tool_use_id]: request.id });
          }
        } else if (request.operation === "compact_conversation") {
          const resume = request.input.resume === true;
          if (resume && (await $.session.surfaces()).length === 0) {
            reason = "圧縮後の自動再開は対話画面がある時だけ予約できる。圧縮だけが必要ならcompact_conversationを引数なしで再実行せよ。";
          } else {
            await $.state.set({ plugin: "agent-toolkit", key: "compactRequested" }, resume ? "resume" : true);
            ok = true;
            reason = "圧縮を予約した。完了ではない。現在のターンを終えてホストの結果を待て。";
          }
        } else if (request.operation === "interactive_available") {
          try {
            interactive = (await $.session.surfaces()).length > 0;
            version = (await $.session.version()).version;
            const mode = await $.settings.read({ key: "autoMode" });
            if (mode === undefined) autoMode = {};
            else if (mode && typeof mode === "object" && !Array.isArray(mode)) {
              const values = mode as Record<string, unknown>;
              autoMode = {};
              if (Object.hasOwn(values, "hard_deny")) autoMode.hard_deny = values.hard_deny ?? null;
              if (Object.hasOwn(values, "soft_deny")) autoMode.soft_deny = values.soft_deny ?? null;
            }
            flagAutoModePresent = (await $.settings.read({ source: "flag", key: "autoMode" })) !== undefined;
            ok = true;
            reason = interactive ? "対話画面と実効autoModeの取得結果を返した。" : "対話画面がない。";
          } catch (error) { reason = `実行中のautoMode設定を確認できない。${String(error)}`; }
        } else if (request.operation === "repl_bash_started" && typeof request.input.id === "string" && typeof request.input.command === "string" &&
                   typeof request.input.expires === "number" && Number.isFinite(request.input.expires)) {
          const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
          if (!Object.hasOwn(waits, request.input.id) && request.input.expires * 1000 > Date.now()) {
            const wait: BackgroundWait = { id: request.input.id, command: request.input.command, startedAt: await $.clock.now(), used: false, replExpiresAt: request.input.expires * 1000 };
            await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, { ...waits, [wait.id]: wait });
            expireReplWait($, wait);
            session.backgroundChanged = true;
          }
          ok = true;
        } else if (request.operation === "background_task" && typeof request.input.id === "string" && typeof request.input.command === "string") {
          const seen = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundSeen" })).value ?? {};
          if (!Object.hasOwn(seen, request.input.id)) {
            const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
            await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, { ...waits, [request.input.id]: { id: request.input.id, command: request.input.command, startedAt: await $.clock.now(), used: false } });
            await $.state.set({ plugin: "agent-toolkit", key: "backgroundSeen" }, { ...seen, [request.input.id]: true });
            session.backgroundChanged = true;
          }
          ok = true;
        } else if (request.operation === "periodic_recheck") {
          reason = await mountPeriodicRecheck($);
          ok = true;
        } else if (request.operation === "periodic_recheck_collect") {
          // Stopが全待機の終了を判定した時点で回収し、ターンの終了直後のidleで実行中に発火時刻を迎えた発火分が届くのを防ぐ。
          // 削除できなかった場合は`turn.complete`の回収が再評価して警告を届けるため、ここでは理由を応答へ返すだけにする。
          reason = (await collectCommonTasks($)) ?? "";
          ok = true;
        } else if (request.operation === "background_complete" && typeof request.input.id === "string") {
          const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
          delete waits[request.input.id];
          await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, waits);
          ok = true;
        } else if (request.operation === "background_snapshot" && Array.isArray(request.input.ids) && request.input.ids.every((id) => typeof id === "string")) {
          const active = new Set(request.input.ids);
          const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
          await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, Object.fromEntries(Object.entries(waits).filter(([id, wait]) => wait.replExpiresAt !== undefined || active.has(id))));
          ok = true;
        } else if (request.operation === "apply_runtime_changes" && Array.isArray(request.input?.targets) &&
                   request.input.targets.length > 0 && request.input.targets.every((target): target is string => typeof target === "string" && ["plugins", "agents_server", "pyfltr"].includes(target)) && new Set(request.input.targets).size === request.input.targets.length) {
          const pending = (await $.state.get({ plugin: "agent-toolkit", key: "runtimeChange" })).value;
          if (pending && pending.phase !== "done") reason = "未完了の反映予約がある。";
          else {
            await $.state.set({ plugin: "agent-toolkit", key: "runtimeChange" }, { id: request.id, targets: request.input.targets, phase: "pending", results: [], notified: false });
            ok = true;
            reason = "変更反映を予約した。まだ反映完了ではない。現在のターンを終えて反映結果を待て。" +
              (request.input.targets.includes("agents_server") ? "agents_serverの再接続は実行中の子turnを中断し、既存の復旧で同じ会話を再開する。" : "");
          }
        }
        if (request.expires * 1000 > Date.now() && await $.fs.exists(source)) {
          const response = responseBase.replace(/\.txt$/, `-${request.id}.txt`);
          await $.fs.write(response, JSON.stringify({ id: request.id, ok, reason, interactive, version, auto_mode: autoMode, flag_auto_mode_present: flagAutoModePresent, expires: request.expires }));
          if (request.detached) cleanupNeeded = true;
        } else if (request.operation === "periodic_recheck" && reason) {
          // hookが応答の待機を諦めた後の装着失敗は、失わないよう次のターンの入力として届ける。
          await startNextTurn($, reason);
        }
        }
        if (cleanupNeeded && !cleanupFailed) {
          try {
            const cleaned = await $.process.run(pluginLaunchCommand($.plugin.root, "agent_toolkit/_common/function_hook_requests.py", ["--cleanup-session", sessionId]), { timeoutMs: 30_000 });
            if (cleaned.exitCode !== 0 || cleaned.isStdoutTruncated || cleaned.isStderrTruncated || JSON.parse(cleaned.stdout)?.complete !== true) throw new Error("回収の確認応答がない");
          } catch (error) {
            cleanupFailed = true;
            $.ui.toast(`会話内の一時要求を回収できない。通常の要求処理は続ける。保存先の権限と次の診断を確認せよ: ${String(error)}`);
          }
        }
      } finally { busy = false; }
    });
    return next(e);
  });
  on("ui.render", { component: "Placed" }, async ($, e, next) => {
    if (e.props.plugin !== $.plugin.name) return next(e);
    const stored = (await $.state.get({ plugin: "agent-toolkit", key: "uiMessages" })).value?.[e.props.id];
    if (stored === undefined) return next(e);
    const { Markdown, Box } = $.ui.resolve(e);
    // 前後の会話行と区別しやすいよう、本文の上下に1行の余白を置く。
    return (
      <Box flexDirection="column" marginTop={1} marginBottom={1}>
        <Markdown text={placedMessage(stored)} />
      </Box>
    );
  });
  // 置いた行と会話状態は会話の終了・/resume・/branchで失われる。再開後の画面では、transcriptに残る
  // send_to_userのツール入力から元の位置へ本文を描く。稼働中に置いた行がある呼出しは二重に描かない。
  on("ui.render", { component: "ToolGroup" }, async ($, e, next) => {
    const calls = e.props.calls.filter((call) => isSendToUser(call.tool) && deliveredMessage(call) !== undefined);
    if (calls.length === 0 || e.props.isExpanded) return next(e);
    if ((await Promise.all(calls.map((call) => placedLive($, call)))).every(Boolean)) return next(e);
    return next({ ...e, props: { ...e.props, isExpanded: true } });
  });
  on("ui.render", { component: "ToolUse" }, async ($, e, next) => {
    if (!isSendToUser(e.props.tool)) return next(e);
    const message = deliveredMessage(e.props);
    if (message === undefined) return next(e);
    if (await placedLive($, e.props)) return next(e);
    const { Markdown, Box } = $.ui.resolve(e);
    return (
      <Box marginTop={1} marginBottom={1}>
        <Markdown text={message} />
      </Box>
    );
  });
}

// agents_server MCPのsend_to_userだけを対象にし、他のツールの行は描き替えない。
function isSendToUser(tool: string): boolean {
  return /agents_server__send_to_user$/.test(tool);
}

export function placedMessage(stored: string | { message: string; placedAt: number }): string {
  return typeof stored === "string" ? stored : stored.message;
}

type SendCall = { tool_use_id?: string; input: unknown; isRunning: boolean; isErrored: boolean; isInterrupted: boolean };

// この会話の稼働中に行を置いた呼出しかを返す。会話状態は再開で初期化されるため、再開前の呼出しは偽になる。
// 要求がtool_use_idを持つ場合は呼出しで照合し、再開後に同じ本文を送っても再開前の呼出しの表示を消さない。
// tool_use_idを持たない要求（MCP要求の_metaに無い場合）だけを本文の一致で照合する。
async function placedLive($: EngineInterface, call: SendCall): Promise<boolean> {
  const messages = (await $.state.get({ plugin: "agent-toolkit", key: "uiMessages" })).value ?? {};
  const calls = (await $.state.get({ plugin: "agent-toolkit", key: "uiCalls" })).value ?? {};
  if (call.tool_use_id !== undefined && Object.hasOwn(calls, call.tool_use_id)) return Object.hasOwn(messages, calls[call.tool_use_id] ?? "");
  const keyed = new Set(Object.values(calls));
  return Object.entries(messages).some(([id, message]) => !keyed.has(id) && placedMessage(message) === deliveredMessage(call));
}

// 表示に成功した呼び出しの本文を返す。実行中・失敗・中断の呼び出しは本文を描かない。
function deliveredMessage(call: { input: unknown; isRunning: boolean; isErrored: boolean; isInterrupted: boolean }): string | undefined {
  if (call.isRunning || call.isErrored || call.isInterrupted) return undefined;
  const message = (call.input as { message?: unknown } | null)?.message;
  return typeof message === "string" ? message : undefined;
}
