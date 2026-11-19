import type { On } from "claude-code";
import { automatedMessage, startNextTurn } from "./automated_message.ts";
import type { RuntimeChange } from "./ui_requests.tsx";

export function register(on: On): void {
  on("turn.complete", { turnId: /.+/ }, async ($, e, next) => {
    const result = await next(e);
    if (e.agentId !== undefined || e.isAborted) return result;
    const held = (await $.state.get({ plugin: "agent-toolkit", key: "runtimeChange" })).value;
    if (!held || held.phase !== "pending") return result;
    const change: RuntimeChange = { ...held, phase: "running" };
    // turn完了hookが戻る前に消費し、再読込後にコマンドを二重実行しない。
    await $.state.set({ plugin: "agent-toolkit", key: "runtimeChange" }, change);
    $.clock.after(0, async () => {
      // 自身のmoduleを再読込する操作は、MCP再接続を記録した後で行う。
      const targets = [...change.targets.filter((target) => target !== "plugins"), ...change.targets.filter((target) => target === "plugins")];
      for (const target of targets) {
        try {
          const outcome = target === "plugins"
            ? await $.command.run({ command: "reload-plugins" })
            : await $.command.run({ command: "mcp", args: `reconnect plugin:agent-toolkit:${target}` });
          change.results.push({ target, ...outcome });
        } catch (error) {
          change.results.push({ target, error: String(error) });
        }
        const current = (await $.state.get({ plugin: "agent-toolkit", key: "runtimeChange" })).value;
        if (current?.id !== change.id || current.phase === "done") return;
        change.notified = current.notified;
        await $.state.set({ plugin: "agent-toolkit", key: "runtimeChange" }, change);
      }
      change.phase = "done";
      const latest = (await $.state.get({ plugin: "agent-toolkit", key: "runtimeChange" })).value;
      change.notified = latest?.notified ?? false;
      if (!change.notified) {
        change.notified = true;
        await $.state.set({ plugin: "agent-toolkit", key: "runtimeChange" }, change);
        await startNextTurn($, automatedMessage("runtime-changes", "notice", "反映コマンドの応答を受け取った。受付や応答到着だけを成功とせず、次のtext/contextで成功・失敗・未確認を区別して残工程を続けよ。\n" + JSON.stringify(change.results)));
      } else await $.state.set({ plugin: "agent-toolkit", key: "runtimeChange" }, change);
    });
    return result;
  });
}
