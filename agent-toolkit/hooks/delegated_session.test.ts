import { type Engine, expect, mock, test } from "claude-code/testing";
import type { On } from "claude-code";

// `register.ts`の委譲先の判定が`agent_toolkit/_common/delegated_session.py`の`is_delegated`と同じ結果を返すことを、
// `session.start`によるメイン用ツールの登録から検証する。判定がずれると、委譲先へメイン用ツールが公開されるか、メインから失われる。
// 入力表の先頭4行は`delegated_session_test.py`と同じ入力と期待値である。

const MAIN_ONLY_TOOLS = ["send_to_user", "compact_conversation"];

const CASES: { env: Readonly<Record<string, string>>; delegated: boolean }[] = [
  { env: { AGENT_TOOLKIT_DELEGATED_SESSION: "1" }, delegated: true },
  { env: { AGENT_TOOLKIT_OWNER_SESSION: "owner" }, delegated: true },
  { env: {}, delegated: false },
  { env: { AGENT_TOOLKIT_DELEGATED_SESSION: "0" }, delegated: false },
  // agents_serverのClaude委譲先が持つ組合せ。
  { env: { AGENT_TOOLKIT_DELEGATED_SESSION: "1", AGENT_TOOLKIT_OWNER_SESSION: "owner" }, delegated: true },
];

// `session.start`を呼び、登録されたツール名を返す。
async function registeredTools($: Engine, on: On): Promise<string[]> {
  const names: string[] = [];
  on("session.start", (_$, e) => ({ cwd: e.cwd }));
  on("session.id", () => ({ value: "session-1" }));
  on("fs.write", () => ({ value: undefined }));
  on("tool.register", (_$, e) => {
    names.push(e.name);
    return { value: { tool: e.name } };
  });
  await $.session.start({ cwd: "/work", surface: null, isInteractive: false });
  return names;
}

for (const { env, delegated } of CASES) {
  test(`${JSON.stringify(env)}のsession.startは${delegated ? "メイン用ツールを登録しない" : "メイン用ツールを登録する"}`, async ($, on) => {
    mock.env(on, env);
    const names = await registeredTools($, on);
    for (const tool of MAIN_ONLY_TOOLS) expect(names.includes(tool)).toBe(!delegated);
  });
}
