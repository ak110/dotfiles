import type { On } from "claude-code";
import { exitStatePath } from "./session_exit.ts";
import { pluginLaunchCommand } from "./plugin_launch.ts";

export function register(on: On, session: { delegated: boolean; processLoop?: boolean }): void {
  on("command.run", { command: /^(restart|update)$/ }, async ($, e, next) => {
    // ホストの自動実行制限を保ち、ユーザーの再起動だけを補助する。
    if (e.origin.kind !== "composer" || session.delegated) return next(e);
    const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
    const stopped: { id: string; command: string }[] = [];
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const path = exitStatePath(await $.session.id(), await $.env.get("CLAUDE_CONFIG_DIR"), home, "restart");
    if (!path) return { text: "再開予約の保存先を解決できないため再起動していない。会話IDとHOMEまたはUSERPROFILEの取得可否を、この診断とともにプラグイン管理者へ報告せよ。" };
    try {
      for (const wait of Object.values(waits)) {
        if (wait.replExpiresAt !== undefined) continue;
        const checked = await $.process.run(pluginLaunchCommand($.plugin.root, "agent_toolkit/atk.py", [
          "run-script", "wait-compaction-context", "--", "--session-id", await $.session.id(), `--command=${wait.command}`,
        ]), { timeoutMs: 30_000 });
        if (checked.exitCode !== 0 || checked.isStdoutTruncated || checked.isStderrTruncated) return { text: "背景待機の所有を確認できないため再起動していない。/tasksで背景待機を確認し、待機の終了後に同じ再起動コマンドを実行せよ。" };
        const context: unknown = JSON.parse(checked.stdout);
        // 一般背景Bashもeligibleになり得る。所有子を持つ単独atk agents waitだけがsessionsを返す。
        if (!context || typeof context !== "object" || !("eligible" in context) || context.eligible !== true ||
            !("sessions" in context) || !Array.isArray(context.sessions) || !context.sessions.length) continue;
        const input = { task_id: wait.id };
        if ((await $.tool.check({ tool: "TaskStop", input })).decision !== "allow") return { text: "背景待機の停止を許可されていないため再起動していない。停止の拒否を保持し、/tasksで待機の終了を確認してから同じ再起動コマンドを実行せよ。" };
        const outcome = await $.tool.call({ tool: "TaskStop", ...input });
        const result: unknown = outcome.result;
        if (outcome.deny !== undefined || outcome.isError === true || !result || typeof result !== "object" ||
            !("task_id" in result) || result.task_id !== wait.id || !("message" in result) || typeof result.message !== "string" ||
            !result.message.startsWith("Successfully stopped task")) return { text: "背景待機の終了を確認できないため再起動していない。停止済みの待機と残る待機を確認せよ。" };
        stopped.push({ id: wait.id, command: wait.command });
        delete waits[wait.id];
        await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, waits);
      }
      const recorded = stopped.length > 0 || session.processLoop === true;
      if (recorded) await $.fs.write(path, JSON.stringify({ phase: "pending", waits: stopped }));
      try {
        const result = await next(e);
        if (recorded && ((result.exitCode !== undefined && result.exitCode !== 0) || /can't (?:restart|update)/i.test(result.text ?? ""))) {
          await $.fs.write(path, JSON.stringify({ phase: "resumed", waits: [] }));
        }
        return result;
      } catch (error) {
        if (recorded) await $.fs.write(path, JSON.stringify({ phase: "resumed", waits: [] }));
        throw error;
      }
    } catch (error) {
      return { text: `再起動の準備を完了できないため再起動していない。${String(error)}。この診断と/tasksの背景タスク一覧をプラグイン管理者へ報告せよ。` };
    }
  });
}
