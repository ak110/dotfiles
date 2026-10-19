import { expect, mock, test } from "claude-code/testing";

// send_to_userの呼び出しの行が`message`の全文を`Markdown`で描き、他のツールの行を描き替えないことを検証する。
// 行を描き替えないと、ツール入力で運んだ原文がユーザーの画面へ届かず、要約に置き換わる地の文と同じ欠落が残る。
// 委譲先の各印では許可も成功応答もしないことも検証する。委譲先の応答は委譲元へ返り、画面へは届かない。
// 委譲先への登録の除外は`delegated_session.test.ts`が検証する。

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

for (const env of DELEGATED_ENVS) {
  test(`委譲先（${JSON.stringify(env)}）のsend_to_userは許可も表示済みの応答もしない`, async ($, on) => {
    mock.env(on, env);
    expect((await $.tool.check({ tool: TOOL, input: { message: MESSAGE } })).decision).toBe("deny");
    const called = await $.tool.call({ tool: TOOL, message: MESSAGE });
    expect(called.result).toBeUndefined();
    // 拒否の本文は、結果本文を委譲元へ返すことと、終端の返却へ書くことを案内する。
    expect(called.deny).toMatch(/^<atk-auto source="main-only-tool" kind="block">\n[\s\S]*\n<\/atk-auto>$/);
    expect(called.deny).toMatch("委譲元へ返す");
    expect(called.deny).toMatch("終端の返却");
  });
}

test("send_to_userの呼び出しは許可され、確認応答を返す", async ($, on) => {
  mock.env(on, {});
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  const { decision } = await $.tool.check({ tool: TOOL, input: { message: MESSAGE } });
  expect(decision).toBe("allow");
  const called = await $.tool.call({ tool: TOOL, message: MESSAGE });
  expect(called.deny).toBeUndefined();
  expect(called.isError).toBeUndefined();
  expect(called.result).toBe("ユーザーの画面へ表示した。");
});

test("サブエージェントのsend_to_userは拒否し、結果本文を最終応答で委譲元へ返すよう案内する", async ($, on) => {
  mock.env(on, {});
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  expect((await $.tool.check({ tool: TOOL, agentId: "child", input: { message: MESSAGE } })).decision).toBe("deny");
  const called = await $.tool.call({ tool: TOOL, agentId: "child", message: MESSAGE });
  expect(called.result).toBeUndefined();
  expect(called.deny).toMatch(/^<atk-auto source="main-only-tool" kind="block">\n[\s\S]*\n<\/atk-auto>$/);
  expect(called.deny).toMatch("委譲元へ返す");
  expect(called.deny).toMatch("最終応答");
});

test("send_to_userは文字列のmessageが無ければ表示済みと返さない", async ($, on) => {
  mock.env(on, {});
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  for (const input of [{ text: MESSAGE }, {}, { message: null }, { message: 1 }, { message: {} }]) {
    const called = await $.tool.call({ tool: TOOL, ...input });
    expect(called.deny).toMatch(/^<atk-auto source="send-to-user" kind="block">\n[\s\S]*\n<\/atk-auto>$/);
    expect(called.result).toBeUndefined();
  }
});

test("send_to_userは空文字のmessageも描画と同じ条件で受け取る", async ($, on) => {
  mock.env(on, {});
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
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

test("英語主体のmessageは表示せず書き直しを求める", async ($, on) => {
  mock.env(on, {});
  on("process.run", (_$, e) => {
    expect(e.argv).toContain("--text=The work is complete.");
    return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: "warn", body: "英語主体の本文。", fix: "日本語で書き直し、識別子と名前を確かめる。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
  });
  const result = await $.tool.call({ tool: TOOL, message: "The work is complete." });
  expect(result.result).toBeUndefined();
  expect(result.deny).toContain("日本語で書き直し");
});

test("言語判定中と拒否・中断後のToolUseに入力本文を描かない", async ($, on) => {
  mock.env(on, {});
  const message = "The work is complete.";
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ outcome: "warn", body: "英語主体の本文。", fix: "日本語で書き直す。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  const denied = await $.tool.call({ tool: TOOL, message });
  expect(denied.result).toBeUndefined();
  expect(denied.deny).toContain("日本語で書き直す");
  for (const surface of ["terminal", "desktop"] as const) {
    for (const status of [
      { isRunning: true, isErrored: false, isInterrupted: false, output: undefined },
      { isRunning: false, isErrored: true, isInterrupted: false, output: denied.deny },
      { isRunning: false, isErrored: false, isInterrupted: true, output: "中断" },
    ]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: "ToolUse", requestId: "toolu_1", props: { ...row(TOOL, { message }), ...status } });
      expect(await ui.find({ type: "Markdown" })).toBeUndefined();
      expect(await ui.find({ type: "Box" })).toBeDefined();
      await ui.unmount();
    }
  }
});

test("日本語の呼び出しが成功した後のToolUseに本文を描く", async ($, on) => {
  mock.env(on, {});
  on("process.run", () => ({ value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
  const message = "確認と検証が完了しました。";
  const accepted = await $.tool.call({ tool: TOOL, message });
  expect(accepted.result).toBe("ユーザーの画面へ表示した。");
  for (const surface of ["terminal", "desktop"] as const) {
    const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: "ToolUse", requestId: "toolu_1", props: { ...row(TOOL, { message }), output: accepted.result } });
    expect((await ui.find({ type: "Markdown" }))?.props.text).toBe(message);
    await ui.unmount();
  }
});

// 判定基盤の障害は日本語へ書き直しても解消しないため、拒否すると報告の本文がユーザーへ届かない。
// 障害の形ごとに、成功結果と本文の描画が成立し、診断が同じ会話へ1回だけ届くことを確かめる。
const PASS_RESPONSE = { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false };
const SECRET_STDERR = "入力本文を含めない";
const JAPANESE_MESSAGE = "確認が完了しました。";

for (const [name, response, expected] of [
  ["非0終了", { ...PASS_RESPONSE, exitCode: 7, stdout: "", stderr: SECRET_STDERR }, "終了コード 7"],
  ["標準出力の切り詰め", { ...PASS_RESPONSE, stdout: "", isStdoutTruncated: true }, "標準出力の切り詰め true"],
  ["標準エラーの切り詰め", { ...PASS_RESPONSE, stdout: "", isStderrTruncated: true }, "標準エラーの切り詰め true"],
  ["JSON破損", { ...PASS_RESPONSE, stdout: "broken-json" }, "JSONとして不正"],
  ["JSON契約外", { ...PASS_RESPONSE, stdout: "{}" }, "outcome・body・fixが契約外"],
  ["process.runの例外", undefined, "process.runで例外が発生"],
] as const) {
  test(`言語判定の${name}では本文を表示し、診断を同じ会話へ1回だけ届ける`, async ($, on) => {
    mock.env(on, {});
    on("session.id", () => ({ value: "send-test" }));
    let failing = true;
    on("process.run", () => {
      if (!failing) return { value: PASS_RESPONSE };
      if (response === undefined) throw new Error("判定処理の障害");
      return { value: response };
    });
    const called = await $.tool.call({ tool: TOOL, message: JAPANESE_MESSAGE });
    expect(called.deny).toBeUndefined();
    expect(called.result).toBe("ユーザーの画面へ表示した。");
    expect(called.context?.length).toBe(1);
    const notice = called.context?.[0] ?? "";
    expect(notice).toMatch(/^<atk-auto source="response-language" kind="warn">\n[\s\S]*\n<\/atk-auto>$/);
    expect(notice).toContain(expected);
    expect(notice).toContain("ユーザーへ報告する");
    expect(notice).not.toContain(JAPANESE_MESSAGE);
    expect(notice).not.toContain(SECRET_STDERR);
    expect(notice).not.toContain("broken-json");
    for (const surface of ["terminal", "desktop"] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: "ToolUse", requestId: "toolu_1", props: { ...row(TOOL, { message: JAPANESE_MESSAGE }), output: called.result } });
      expect((await ui.find({ type: "Markdown" }))?.props.text).toBe(JAPANESE_MESSAGE);
      await ui.unmount();
    }
    const repeated = await $.tool.call({ tool: TOOL, message: JAPANESE_MESSAGE });
    expect(repeated.result).toBe("ユーザーの画面へ表示した。");
    expect(repeated.context ?? []).toEqual([]);
    failing = false;
    const recovered = await $.tool.call({ tool: TOOL, message: JAPANESE_MESSAGE });
    expect(recovered.result).toBe("ユーザーの画面へ表示した。");
    expect(recovered.context ?? []).toEqual([]);
  });
}

test("判定不能の後の英語のmessageは書き直しを求め、書き直し後は表示する", async ($, on) => {
  mock.env(on, {});
  on("session.id", () => ({ value: "send-test" }));
  let failing = true;
  on("process.run", (_$, e) => {
    if (failing) return { value: { ...PASS_RESPONSE, exitCode: 7 } };
    const english = e.argv.at(-1) === "--text=The work is complete.";
    return { value: { ...PASS_RESPONSE, stdout: JSON.stringify({ outcome: english ? "warn" : "pass", body: "英語主体の本文。", fix: "日本語で書き直す。" }) } };
  });
  expect((await $.tool.call({ tool: TOOL, message: JAPANESE_MESSAGE })).result).toBe("ユーザーの画面へ表示した。");
  failing = false;
  const denied = await $.tool.call({ tool: TOOL, message: "The work is complete." });
  expect(denied.result).toBeUndefined();
  expect(denied.deny).toMatch(/^<atk-auto source="response-language" kind="block">\n[\s\S]*\n<\/atk-auto>$/);
  expect(denied.deny).toContain("日本語で書き直す");
  const rewritten = await $.tool.call({ tool: TOOL, message: "作業が完了した。" });
  expect(rewritten.result).toBe("ユーザーの画面へ表示した。");
  expect(rewritten.context ?? []).toEqual([]);
});

for (const caller of [undefined, "", "/caller/project-environment"] as const) {
  test(`呼出元UV_PROJECT_ENVIRONMENT=${String(caller)}でも専用ランチャーを使う`, async ($, on) => {
    mock.env(on, caller === undefined ? {} : { UV_PROJECT_ENVIRONMENT: caller });
    const seen: string[][] = [];
    on("process.run", (_$, e) => {
      seen.push([...e.argv]);
      return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    const result = await $.tool.call({ tool: TOOL, message: "確認しました。" });
    expect(result.result).toBe("ユーザーの画面へ表示した。");
    expect(seen.length).toBe(1);
    const argv = seen[0]!;
    expect(argv.slice(0, 7)).toEqual(["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python"]);
    expect(argv[7]!.endsWith("/scripts/launch.py")).toBe(true);
    expect(argv.slice(8)).toEqual(["agent_toolkit/_common/response_language_check.py", "--text=確認しました。"]);
  });
}

for (const message of ["--help", "--text", "", "--Next verify", "-x\n日本語の本文", "--name=空 白"] as const) {
  test(`オプションに見える本文も単一の値引数で渡す: ${JSON.stringify(message)}`, async ($, on) => {
    mock.env(on, {});
    const seen: string[][] = [];
    on("process.run", (_$, e) => {
      seen.push([...e.argv]);
      return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: "skip", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    const result = await $.tool.call({ tool: TOOL, message });
    expect(result.result).toBe("ユーザーの画面へ表示した。");
    expect(seen.length).toBe(1);
    expect(seen[0]!.slice(8)).toEqual(["agent_toolkit/_common/response_language_check.py", `--text=${message}`]);
  });
}
