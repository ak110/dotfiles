import { expect, mock, test } from "claude-code/testing";

// send_to_userの呼び出しの行が`message`の全文を`Markdown`で描き、他のツールの行を描き替えないことを検証する。
// 行を描き替えないと、ツール入力で運んだ原文がユーザーの画面へ届かず、要約に置き換わる地の文と同じ欠落が残る。
// 委譲先の各印では許可も成功応答もしないことも検証する。委譲先の応答は委譲元へ返り、画面へは届かない。
// 委譲先への登録の除外は`delegated_session.test.ts`が検証する。

const PLUGIN = "agent-toolkit";
const TOOL = `mcp__${PLUGIN}__send_to_user`;
const DISPLAYED = "ユーザーの画面へ表示した。同じ本文の再掲は省き、残工程があれば続行せよ。伝える内容と残工程がない通常終了は、rules-main.claude-code.md「ターンの終え方」に従い省略記号で閉じよ。";
const MESSAGE = "## 作業完了報告\n\n- 変更: `a.py`\n- 検証: 成功\n\n次の工程へ進む。\n\n".repeat(30);

function row(tool: string, input: unknown) {
  return {
    tool_use_id: "toolu_1",
    tool,
    input,
    isRunning: false,
    isErrored: false,
    isInterrupted: false,
    output: DISPLAYED,
  };
}

test("send_to_userの行はmessageの全文を前に空行1行分の余白を置いたMarkdownで描く", async ($) => {
  // 余白が無いと直前の表示と本文が続いて読みにくい。本文の欠落と余白の欠落の両方を検出する。
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
    expect(await ui.drawn()).toMatchObject({ type: "Box", props: { marginTop: 1 } });
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
    // 拒否の本文は、結果本文を委譲元へ返すことと、終了の返却へ書くことを案内する。
    expect(called.deny).toMatch(/^<atk-auto source="main-only-tool" kind="block">\n[\s\S]*\n<\/atk-auto>$/);
    expect(called.deny).toMatch("委譲元へ返す");
    expect(called.deny).toMatch("終了の返却");
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
  expect(called.result).toBe(DISPLAYED);
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
  expect(called.result).toBe(DISPLAYED);
});

test("send_to_userの成功結果は応答文言によらず空のBoxで描く", async ($) => {
  for (const surface of ["terminal", "desktop"] as const) {
    for (const output of [DISPLAYED, "別の成功結果"]) {
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
  expect(accepted.result).toBe(DISPLAYED);
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

for (const [name, message, notice, expectedCalls] of [
  ["未取得の改善点", "気付いた改善点: A（AWI化ステータス: 不要 非欠陥）", "<atk-auto source=\"improvement_status_advisor\" kind=\"warn\">\n判定資料を読め。\n</atk-auto>", 1],
  ["取得済みの改善点", "気付いた改善点: A（AWI化ステータス: 不要 非欠陥）", "", 1],
  ["点検の改善案", "改善案:\n- A（AWI化ステータス: 不要 非欠陥）", "案内", 1],
  ["説明文", "AWI化ステータス: 不要の区分を調べた。", "案内", 0],
] as const) {
  test(`send_to_userは${name}で判定資料の取得案内を非遮断で返す`, async ($, on) => {
    mock.env(on, {});
    on("session.id", () => ({ value: "guidance-session" }));
    let called = 0;
    on("process.run", (_$, e) => {
      if (!e.argv.includes("improvement_status_advisor")) return { value: PASS_RESPONSE };
      called += 1;
      const payload: unknown = JSON.parse(e.init?.stdin ?? "{}");
      expect(payload).toMatchObject({ session_id: "guidance-session" });
      expect((payload as { last_assistant_message: string }).last_assistant_message).toContain("気付いた改善点:");
      return { value: { ...PASS_RESPONSE, stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: notice } }) } };
    });
    const result = await $.tool.call({ tool: TOOL, message });
    expect(result.result).toBe(DISPLAYED);
    expect(result.deny).toBeUndefined();
    expect(called).toBe(expectedCalls);
    expect(result.context ?? []).toEqual(expectedCalls && notice ? [notice] : []);
  });
}

test("取得案内の障害は送達を止めず、同じ診断を抑止し復旧後は再通知する", async ($, on) => {
  mock.env(on, {});
  on("session.id", () => ({ value: "guidance-failure-session" }));
  let failing = true;
  on("process.run", (_$, e) => ({ value: e.argv.includes("improvement_status_advisor")
    ? { ...PASS_RESPONSE, exitCode: failing ? 7 : 0, stderr: SECRET_STDERR,
      stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: "" } }) }
    : PASS_RESPONSE }));
  const message = "気付いた改善点: A（AWI化ステータス: 不要 非欠陥）";
  const first = await $.tool.call({ tool: TOOL, message });
  expect(first.result).toBe(DISPLAYED);
  expect(first.deny).toBeUndefined();
  expect(first.context?.[0]).toContain("終了コード 7");
  expect(first.context?.[0]).not.toContain(SECRET_STDERR);
  expect((await $.tool.call({ tool: TOOL, message })).context ?? []).toEqual([]);
  failing = false;
  expect((await $.tool.call({ tool: TOOL, message })).context ?? []).toEqual([]);
  failing = true;
  expect((await $.tool.call({ tool: TOOL, message })).context).toHaveLength(1);
});

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
    expect(called.result).toBe(DISPLAYED);
    expect(called.context?.length).toBe(1);
    const notice = called.context?.[0] ?? "";
    expect(notice).toMatch(/^<atk-auto source="response-language" kind="warn">\n[\s\S]*\n<\/atk-auto>$/);
    expect(notice).toContain(expected);
    expect(notice).toContain("ユーザーへ報告");
    expect(notice).not.toContain(JAPANESE_MESSAGE);
    expect(notice).not.toContain(SECRET_STDERR);
    expect(notice).not.toContain("broken-json");
    for (const surface of ["terminal", "desktop"] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: "ToolUse", requestId: "toolu_1", props: { ...row(TOOL, { message: JAPANESE_MESSAGE }), output: called.result } });
      expect((await ui.find({ type: "Markdown" }))?.props.text).toBe(JAPANESE_MESSAGE);
      await ui.unmount();
    }
    const repeated = await $.tool.call({ tool: TOOL, message: JAPANESE_MESSAGE });
    expect(repeated.result).toBe(DISPLAYED);
    expect(repeated.context ?? []).toEqual([]);
    failing = false;
    const recovered = await $.tool.call({ tool: TOOL, message: JAPANESE_MESSAGE });
    expect(recovered.result).toBe(DISPLAYED);
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
  expect((await $.tool.call({ tool: TOOL, message: JAPANESE_MESSAGE })).result).toBe(DISPLAYED);
  failing = false;
  const denied = await $.tool.call({ tool: TOOL, message: "The work is complete." });
  expect(denied.result).toBeUndefined();
  expect(denied.deny).toMatch(/^<atk-auto source="response-language" kind="block">\n[\s\S]*\n<\/atk-auto>$/);
  expect(denied.deny).toContain("日本語で書き直す");
  const rewritten = await $.tool.call({ tool: TOOL, message: "作業が完了した。" });
  expect(rewritten.result).toBe(DISPLAYED);
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
    expect(result.result).toBe(DISPLAYED);
    expect(seen.length).toBe(1);
    const argv = seen[0]!;
    expect(argv.slice(0, 7)).toEqual(["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python"]);
    expect(argv[7]!.endsWith("/scripts/launch.py")).toBe(true);
    expect(argv.slice(8)).toEqual([argv[7]!.replace(/\/scripts\/launch\.py$/, "/agent_toolkit/_common/response_language_check.py"), "--text=確認しました。"]);
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
    expect(result.result).toBe(DISPLAYED);
    expect(seen.length).toBe(1);
    expect(seen[0]![7]!.endsWith("/scripts/launch.py")).toBe(true);
    expect(seen[0]!.slice(8)).toEqual([seen[0]![7]!.replace(/\/scripts\/launch\.py$/, "/agent_toolkit/_common/response_language_check.py"), `--text=${message}`]);
  });
}

// `AWI化ステータス: 不要`の理由が区分名で始まらない本文だけに警告を付け、どの本文も送達することを検証する。
// 区分外の理由（規範の存在など）で再発防止を省いた報告を送達時に本人へ気付かせるための判定である。
for (const [name, message, warned] of [
  ["区分名で始まらない不要", "改善案:\n- 規範の取り違え（AWI化ステータス: 不要 上位規範から導けたため条文は追加しない）", true],
  ["区分名の無い不要が区分名付きの不要と混在", "気付いた改善点: A（AWI化ステータス: 不要 非欠陥）\n気付いた改善点: B（AWI化ステータス: 不要 当座に直した）", true],
  ["区分名で始まる不要", "気付いた改善点: A（AWI化ステータス: 不要 被覆 20261106-000000-001.md）\n気付いた改善点: B（AWI化ステータス: 不要 見送り 損失より継続費用が大きい）", false],
  ["処置済みと非欠陥", "気付いた改善点: A（AWI化ステータス: 不要 処置済み abc1234）\n気付いた改善点: B（AWI化ステータス: 不要 非欠陥）", false],
  ["不要を含まない本文", "気付いた改善点: A（AWI化ステータス: 済 20261106-000000-001.md）", false],
] as const) {
  test(`send_to_userは${name}の本文を送達し、警告は${warned ? "付ける" : "付けない"}`, async ($, on) => {
    mock.env(on, {});
    on("process.run", (_$, e) => ({ value: e.argv.includes("improvement_status_advisor")
      ? { ...PASS_RESPONSE, stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: "" } }) }
      : PASS_RESPONSE }));
    const called = await $.tool.call({ tool: TOOL, message });
    expect(called.deny).toBeUndefined();
    expect(called.result).toBe(DISPLAYED);
    const warnings = (called.context ?? []).filter((note) => note.startsWith('<atk-auto source="send-to-user"'));
    if (warned) {
      expect(warnings).toHaveLength(1);
      expect(warnings[0]).toMatch(/^<atk-auto source="send-to-user" kind="warn">\n/);
      expect(warnings[0]).toMatch("再発防止策の必須性");
    } else {
      expect(warnings).toEqual([]);
    }
  });
}

// `予定`の区分名の欠落と、原因の未調査を理由にした`不要`へ、それぞれの警告を付けて送達することを検証する。
// コードフェンス内の例と引用行は報告として扱わず、処置を定めた理由や「未調査を理由に見送らない」説明にも付けない。
for (const [name, message, expected] of [
  ["観測した説明文", "2件目（前回batch）の通読も終えた。両セッションで共通に出た「`AWI化ステータス: 不要`の区分名」警告について、既存AWIの有無と原因を確認している。", []],
  ["予定への説明上の言及", "書式はAWI化ステータス: 予定から始める。`AWI化ステータス: 予定`の区分も確認した。", []],
  ["項目記号だけの説明", "- AWI化ステータス: 不要という書き方を調べた。", []],
  ["点検の予定に区分が無い", "改善案:\n- X（AWI化ステータス: 予定 公開後）\n次: 実装", ["待機中の先行実施"]],
  ["改善案なし", "改善案: なし\n次: 検証", []],
  ["字下げコード例", "    気付いた改善点: X（AWI化ステータス: 予定 公開後）", []],
  ["番号付きの報告", "  1) 気付いた改善点: X（AWI化ステータス: 予定 公開後）", ["待機中の先行実施"]],
  ["挿入通知の引用", "<atk-auto source=\"example\" kind=\"notice\">\n気付いた改善点: X（AWI化ステータス: 予定 公開後）\n</atk-auto>", []],
  ["区分名の無い予定", "気付いた改善点: A（AWI化ステータス: 予定 公開完了後に既存AWIと照合して登録要否を判定）", ["待機中の先行実施"]],
  ["区分名で始まる予定", "気付いた改善点: A（AWI化ステータス: 予定 認可待ち ユーザーがpushを認可した後）\n気付いた改善点: B（AWI化ステータス: 予定 書込競合 lane-03の統合後）", []],
  ["済と即時対応だけ", "気付いた改善点: A（AWI化ステータス: 済 20261106-000000-001.md）\n気付いた改善点: B（AWI化ステータス: 即時対応）", []],
  ["観測した未調査理由", "気付いた改善点: 再現しない失敗（AWI化ステータス: 不要 原因が未調査で、このセッションでは再現条件を特定できないため。必要なら調査する）", ["再発防止策の必須性", "原因を調べていない"]],
  ["区分名付きの未調査理由", "気付いた改善点: A（AWI化ステータス: 不要 見送り 原因を調べていないため）", ["原因を調べていない"]],
  ["処置を定めた理由", "気付いた改善点: A（AWI化ステータス: 不要 処置済み abc1234。原因は未確定だが同じ入力で再現しない）", []],
  ["未確定の後に引き継ぎ先を定めた理由", "気付いた改善点: A（AWI化ステータス: 不要 被覆 原因は未確定だが、既存AWI 20261106-174207-001.mdへ観測と調査を引き継ぎ済み）", []],
  ["未調査を理由にしない説明", "気付いた改善点: A（AWI化ステータス: 不要 非欠陥 未調査を理由に見送らず、調査で仕様どおりと確認した）", []],
  ["コードフェンス内の例", "```text\n気付いた改善点: X（AWI化ステータス: 予定 公開後）\n気付いた改善点: Y（AWI化ステータス: 不要 原因が未調査）\n```", []],
  ["引用行", "> 気付いた改善点: X（AWI化ステータス: 不要 原因が未調査）", []],
] as const) {
  test(`send_to_userは${name}の本文を送達し、該当する警告だけを付ける`, async ($, on) => {
    mock.env(on, {});
    on("process.run", (_$, e) => ({ value: e.argv.includes("improvement_status_advisor")
      ? { ...PASS_RESPONSE, stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: "" } }) }
      : PASS_RESPONSE }));
    const called = await $.tool.call({ tool: TOOL, message });
    expect(called.deny).toBeUndefined();
    expect(called.result).toBe(DISPLAYED);
    const warnings = (called.context ?? []).filter((note) => note.startsWith('<atk-auto source="send-to-user"'));
    expect(warnings).toHaveLength(expected.length);
    for (const [index, phrase] of expected.entries()) {
      expect(warnings[index]).toMatch(/^<atk-auto source="send-to-user" kind="warn">\n/);
      expect(warnings[index]).toMatch(phrase);
    }
  });
}
