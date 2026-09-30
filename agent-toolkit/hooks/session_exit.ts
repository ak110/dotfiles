import type { EngineInterface, On } from "claude-code";

const SESSION_ID = /^[A-Za-z0-9_-]+$/;

function isAbsolute(path: string): boolean {
  return path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path);
}

async function statePath($: EngineInterface, sessionId: string, kind: "marker" | "request"): Promise<string | undefined> {
  if (!SESSION_ID.test(sessionId)) return undefined;
  const configured = await $.env.get("CLAUDE_CONFIG_DIR");
  const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
  let root: string | undefined;
  if (configured && isAbsolute(configured)) root = configured;
  else if (home && isAbsolute(home)) root = `${home}/.claude`;
  if (!root) return undefined;
  return `${root}/agent-toolkit-function-hooks/${kind}-${sessionId}.txt`;
}

function isMissing(error: unknown): boolean {
  if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") return true;
  return String(error).includes("ENOENT");
}

// `/exit`は終了とともに停止するセッション限りの作業が残ると確認画面を表示し、無人のセッションはそこで止まる。
// セッション限りのcron taskは終了で消えるため、確認画面で「Exit and stop tasks」を選ぶのと同じ結果になるよう
// `/exit`の直前に削除する。durableなtaskは終了後も残す指定のため削除しない。
// 許可が`allow`でない環境で`$.tool.call`を呼ぶと権限確認ダイアログで同じく止まるため、先に`$.tool.check`で判定する。
// 一覧の取得と削除に失敗しても`/exit`は実行する（結果は削除しない場合の確認画面と同じ）。
async function deleteSessionCrons($: EngineInterface): Promise<void> {
  try {
    if ((await $.tool.check({ tool: "CronList", input: {} })).decision !== "allow") return;
    const listed = await $.tool.call({ tool: "CronList" });
    if (listed.result === undefined) return;
    for (const job of listed.result.jobs) {
      if (job.durable === true) continue;
      if ((await $.tool.check({ tool: "CronDelete", input: { id: job.id } })).decision !== "allow") continue;
      await $.tool.call({ tool: "CronDelete", id: job.id });
    }
  } catch {
    // 削除を諦めて終了要求の処理を続ける。
  }
}

export function register(on: On): void {
  on("session.start", async ($, e, next) => {
    const result = await next(e);
    const sessionId = await $.session.id();
    const marker = await statePath($, sessionId, "marker");
    const request = await statePath($, sessionId, "request");
    if (marker && request) {
      await $.fs.write(request, "consumed");
      await $.fs.write(marker, "ready");
    }
    return result;
  });

  on("turn.complete", async ($, e, next) => {
    const result = await next(e);
    if (e.agentId !== undefined) return result;
    const request = await statePath($, await $.session.id(), "request");
    if (!request) return result;
    let content: string;
    try {
      content = await $.fs.read(request);
    } catch (error) {
      if (isMissing(error)) return result;
      throw error;
    }
    if (content !== "requested") return result;
    await $.fs.write(request, "consumed");
    await deleteSessionCrons($);
    await $.command.run({ command: "exit" });
    return result;
  });
}
