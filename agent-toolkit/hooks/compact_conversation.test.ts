import { expect, mock, test } from "claude-code/testing";

const TOOL = "mcp__agent-toolkit__compact_conversation";

test("受付と重複の応答後に一度だけ圧縮し、完了後は再予約できる", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  const calls: { command: string; args?: string }[] = [];
  on("command.run", (_$, e) => {
    calls.push({ command: e.command, ...(e.args === undefined ? {} : { args: e.args }) });
    return { text: "圧縮完了" };
  });
  expect((await $.tool.check({ tool: TOOL, input: {} })).decision).toBe("allow");
  const first = await $.tool.call({ tool: TOOL });
  expect(first.result).toMatch("まだ完了していない");
  const duplicate = await $.tool.call({ tool: TOOL });
  expect(duplicate.result).toMatch("追加していない");
  expect(calls).toEqual([]);
  await clock.advance(0);
  expect(calls).toEqual([{ command: "compact", args: "" }]);
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toEqual([{ command: "compact", args: "" }, { command: "compact", args: "" }]);
});

test("実行拒否は同じ会話へ通知し、失敗後に再予約できる", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  const prompts: string[] = [];
  let calls = 0;
  on("command.run", () => {
    calls++;
    if (calls === 1) throw new Error("圧縮の実行拒否");
    return { text: "圧縮完了" };
  });
  on("prompt.submit", (_$, e) => {
    prompts.push(e.text);
    return { text: "" };
  });
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(prompts.length).toBe(1);
  expect(prompts[0]?.startsWith('<atk-auto source="compact-conversation" kind="warn">\n')).toBe(true);
  expect(prompts[0]?.endsWith("\n</atk-auto>")).toBe(true);
  // hookの例外はホストが次の実装へ渡す。実装が無い拒否が呼出元へ届く。
  expect(prompts[0]).toMatch("no implementation for command.run");
  await $.tool.call({ tool: TOOL });
  await clock.advance(0);
  expect(calls).toBe(2);
  expect(prompts.length).toBe(1);
});

test("サブエージェントからの呼び出しは予約しない", async ($, on) => {
  mock.env(on, {});
  const clock = mock.clock(on);
  let calls = 0;
  on("command.run", () => {
    calls++;
    return {};
  });
  expect((await $.tool.call({ tool: TOOL, agentId: "child" })).deny).toMatch("メイン");
  await clock.advance(0);
  expect(calls).toBe(0);
});

// `delegated_session.py`の`is_delegated`が委譲先と判定する印の組合せ。委譲先への登録の除外は`delegated_session.test.ts`が検証する。
const DELEGATED_ENVS: Readonly<Record<string, string>>[] = [
  { AGENT_TOOLKIT_DELEGATED_SESSION: "1" },
  { AGENT_TOOLKIT_OWNER_SESSION: "owner" },
  { AGENT_TOOLKIT_DELEGATED_SESSION: "1", AGENT_TOOLKIT_OWNER_SESSION: "owner" },
];

for (const env of DELEGATED_ENVS) {
  test(`委譲先（${JSON.stringify(env)}）からは圧縮を許可も予約もしない`, async ($, on) => {
    mock.env(on, env);
    const clock = mock.clock(on);
    let calls = 0;
    on("command.run", () => { calls++; return { text: "圧縮完了" }; });
    expect((await $.tool.check({ tool: TOOL, input: {} })).decision).toBe("deny");
    expect((await $.tool.call({ tool: TOOL })).deny).toMatch("メイン");
    await clock.advance(0);
    expect(calls).toBe(0);
  });
}
