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
    await $.command.run({ command: "exit" });
    return result;
  });
}
