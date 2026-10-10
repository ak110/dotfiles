import type { On } from "claude-code";
import { type Engine, expect, mock, test } from "claude-code/testing";

// send_to_userの呼び出しの行が`message`の全文を`Markdown`で描き、他のツールの行を描き替えないことを検証する。
// 行を描き替えないと、ツール入力で運んだ原文がユーザーの画面へ届かず、要約に置き換わる地の文と同じ欠落が残る。
// 委譲先の各印では登録・許可・成功応答をしないことも検証する。委譲先の応答は委譲元へ返り、画面へは届かない。

const PLUGIN = "agent-toolkit";
const TOOL = `mcp__${PLUGIN}__send_to_user`;
const MESSAGE = "## 作業完了報告\n\n- 変更: `a.py`\n- 検証: 成功\n\n次の工程へ進む。\n\n".repeat(30);

function row(tool: string, input: unknown) {
  return {
    tool_use_id: "toolu_1",
    tool,
    input,
    isRunning: false,
    isErrored: false,
    isInterrupted: false,
    output: "ユーザーの画面へ表示した。",
  };
}

test("send_to_userの行はmessageの全文をMarkdownで描く", async ($) => {
  for (const surface of ["terminal", "desktop"] as const) {
    const ui = await $.ui.mount({
      plugin: PLUGIN,
      surface,
      component: "ToolUse",
      requestId: "toolu_1",
      props: row(TOOL, { message: MESSAGE }),
    });
    const markdown = await ui.find({ type: "Markdown" });
    expect(markdown?.props.text).toBe(MESSAGE);
    await ui.unmount();
  }
});

test("他のツールの行は描き替えない", async ($, on) => {
  // テストのhookがエンジンの描画を表す。プラグインが後続へ渡せば、この行が描かれる。
  on("ui.render", { component: "ToolUse" }, ($, e) => {
    const { Text } = $.ui.resolve(e);
    return <Text>engine row</Text>;
  });
  for (const surface of ["terminal", "desktop"] as const) {
    const ui = await $.ui.mount({
      plugin: PLUGIN,
      surface,
      component: "ToolUse",
      requestId: "toolu_1",
      props: row("Bash", { command: "ls" }),
    });
    expect(await ui.find({ type: "Markdown" })).toBeUndefined();
    expect((await ui.find({ type: "Text", text: /engine row/ }))?.text).toBe("engine row");
    await ui.unmount();
  }
});

// `delegated_session.py`の`is_delegated`が委譲先と判定する印の組合せ。
const DELEGATED_ENVS: Readonly<Record<string, string>>[] = [
  { AGENT_TOOLKIT_DELEGATED_SESSION: "1" },
  { AGENT_TOOLKIT_OWNER_SESSION: "owner" },
  { AGENT_TOOLKIT_DELEGATED_SESSION: "1", AGENT_TOOLKIT_OWNER_SESSION: "owner" },
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

test("メインのsession.startはsend_to_userを登録する", async ($, on) => {
  mock.env(on, {});
  expect(await registeredTools($, on)).toContain("send_to_user");
});

for (const env of DELEGATED_ENVS) {
  test(`委譲先（${JSON.stringify(env)}）のsession.startはsend_to_userを登録しない`, async ($, on) => {
    mock.env(on, env);
    expect(await registeredTools($, on)).not.toContain("send_to_user");
  });

  test(`委譲先（${JSON.stringify(env)}）のsend_to_userは許可も表示済みの応答もしない`, async ($, on) => {
    mock.env(on, env);
    expect((await $.tool.check({ tool: TOOL, input: { message: MESSAGE } })).decision).toBe("deny");
    const called = await $.tool.call({ tool: TOOL, message: MESSAGE });
    expect(called.result).toBeUndefined();
    // 拒否の本文は、結果本文を委譲元へ返すことと、終端の返却へ書くことを案内する。
    expect(called.deny).toMatch("委譲元へ返す");
    expect(called.deny).toMatch("終端の返却");
  });
}

test("send_to_userの呼び出しは許可され、確認応答を返す", async ($, on) => {
  mock.env(on, {});
  const { decision } = await $.tool.check({ tool: TOOL, input: { message: MESSAGE } });
  expect(decision).toBe("allow");
  const called = await $.tool.call({ tool: TOOL, message: MESSAGE });
  expect(called.deny).toBeUndefined();
  expect(called.isError).toBeUndefined();
  expect(called.result).toBe("ユーザーの画面へ表示した。");
});

test("send_to_userは文字列のmessageが無ければ表示済みと返さない", async ($, on) => {
  mock.env(on, {});
  for (const input of [{ text: MESSAGE }, {}, { message: null }, { message: 1 }, { message: {} }]) {
    const called = await $.tool.call({ tool: TOOL, ...input });
    expect(called.deny).toBeDefined();
    expect(called.result).toBeUndefined();
  }
});

test("send_to_userは空文字のmessageも描画と同じ条件で受け取る", async ($, on) => {
  mock.env(on, {});
  const called = await $.tool.call({ tool: TOOL, message: "" });
  expect(called.deny).toBeUndefined();
  expect(called.isError).toBeUndefined();
  expect(called.result).toBe("ユーザーの画面へ表示した。");
});

test("send_to_userの成功結果は応答文言によらず空のBoxで描く", async ($) => {
  for (const surface of ["terminal", "desktop"] as const) {
    for (const output of ["ユーザーの画面へ表示した。", "別の成功結果"]) {
      const ui = await $.ui.mount({
        plugin: PLUGIN,
        surface,
        component: "ToolResult",
        requestId: "toolu_1",
        props: { ...row(TOOL, { message: MESSAGE }), output },
      });
      expect(await ui.find({ type: "Box" })).toBeDefined();
      expect(await ui.find({ type: "Text" })).toBeUndefined();
      expect(await ui.find({ type: "Markdown" })).toBeUndefined();
      await ui.unmount();
    }
  }
});

test("他ツールとsend_to_userのエラー結果は後続の描画へ渡す", async ($, on) => {
  on("ui.render", { component: "ToolResult" }, ($, e) => {
    const { Text } = $.ui.resolve(e);
    return <Text>engine result</Text>;
  });
  for (const surface of ["terminal", "desktop"] as const) {
    for (const [tool, isErrored] of [["Bash", false], [TOOL, true]] as const) {
      const ui = await $.ui.mount({
        plugin: PLUGIN,
        surface,
        component: "ToolResult",
        requestId: "toolu_1",
        props: { ...row(tool, {}), isErrored },
      });
      expect((await ui.find({ type: "Text", text: /engine result/ }))?.text).toBe("engine result");
      await ui.unmount();
    }
  }
});
