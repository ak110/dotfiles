import { expect, test } from "claude-code/testing";

// send_to_userの呼び出しの行が`message`の全文を`Markdown`で描き、他のツールの行を描き替えないことを検証する。
// 行を描き替えないと、ツール入力で運んだ原文がユーザーの画面へ届かず、要約に置き換わる地の文と同じ欠落が残る。

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

test("send_to_userの呼び出しは許可され、確認応答を返す", async ($) => {
  const { decision } = await $.tool.check({ tool: TOOL, input: { message: MESSAGE } });
  expect(decision).toBe("allow");
  const called = await $.tool.call({ tool: TOOL, message: MESSAGE });
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
