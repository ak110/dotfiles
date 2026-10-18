import type { SessionMessage } from "claude-code";
import { expect, mock, test } from "claude-code/testing";

// 入力の形は公開型定義2.1.277のSessionMessage・ToolUseSummaryに従う。
const ENGLISH = "The investigation is complete and the results have been verified.";
const JAPANESE = "調査と検証が完了したので、結果を確認する。";
const BLOCK_ENVELOPE = /^<atk-auto source="response-language" kind="block">\n[\s\S]*\n<\/atk-auto>$/;

for (const delegated of [false, true]) {
  test(`現在の本文への書き直しと本文無し再発行を判定する（委譲先=${delegated}）`, async ($, on) => {
    mock.env(on, delegated ? { AGENT_TOOLKIT_DELEGATED_SESSION: "1" } : {});
    on("session.id", () => ({ value: "language-test" }));
    let messages: SessionMessage[] = [{ role: "assistant", text: ENGLISH, toolUses: [] }];
    const checked: string[] = [];
    on("session.messages", () => ({ value: messages }));
    on("process.run", (_$, e) => {
      const text = e.argv.at(-1)!.slice("--text=".length);
      checked.push(text);
      return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: text === ENGLISH ? "warn" : "pass", body: "英語主体の本文。", fix: "日本語で書き直し、同じ呼び出しを再発行する。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toContain("日本語で書き直し");
    messages.push({ role: "assistant", text: "", toolUses: [{ tool_use_id: "toolu_1", tool: "Bash", input: { command: "ls" } }] });
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toBeDefined();
    // 保存ファイルの英語が残っていても、実行中の新しい日本語だけを判定する。
    messages.push({ role: "assistant", text: JAPANESE, toolUses: [] });
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    messages.push({ role: "assistant", text: ENGLISH, toolUses: [] });
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toBeDefined();
    expect(checked).toEqual([ENGLISH, ENGLISH, JAPANESE, ENGLISH]);
  });
}

test("send_to_userで書き直した最新本文を後続操作が引き継ぐ", async ($, on) => {
  mock.env(on, {});
  on("session.id", () => ({ value: "language-test" }));
  const messages: SessionMessage[] = [
    { role: "assistant", text: ENGLISH, toolUses: [] },
    { role: "assistant", text: "", toolUses: [{ tool_use_id: "toolu_1", tool: "mcp__agent-toolkit__send_to_user", input: { message: JAPANESE } }] },
    { role: "assistant", text: "", toolUses: [{ tool_use_id: "toolu_2", tool: "Bash", input: { command: "ls" } }] },
  ];
  on("session.messages", () => ({ value: messages }));
  on("process.run", (_$, e) => {
    expect(e.argv.at(-1)).toBe(`--text=${JAPANESE}`);
    return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
  });
  on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
});

test("サブエージェントの実行前判定はメインの本文を読まない", async ($, on) => {
  on("session.messages", () => { throw new Error("メインの本文を取得した"); });
  on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
  const childCall = { tool: "Bash" as const, agentId: "child", command: "ls" };
  expect((await $.tool.call(childCall)).result).toBe("実行済み");
});

for (const outcome of ["pass", "skip"]) {
  test(`${outcome}の同じ本文はCLIを再起動せず、本文とセッションの変化で再判定する`, async ($, on) => {
    let sessionId = "first-session";
    let text = JAPANESE;
    const checked: string[] = [];
    on("session.id", () => ({ value: sessionId }));
    on("session.messages", () => ({ value: [{ role: "assistant", text, toolUses: [] }] }));
    on("process.run", (_$, e) => {
      checked.push(e.argv.at(-1)!);
      return { value: { exitCode: 0, stdout: JSON.stringify({ outcome, body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
    for (let index = 0; index < 3; index++) expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    expect(checked).toEqual([`--text=${JAPANESE}`]);
    text = "新しい本文で次の操作を進める。";
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    text = JAPANESE;
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    sessionId = "second-session";
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    expect(checked).toEqual([`--text=${JAPANESE}`, "--text=新しい本文で次の操作を進める。", `--text=${JAPANESE}`, `--text=${JAPANESE}`]);
  });
}

for (const failed of ["warn", "process-failure"]) {
  test(`${failed}の後は同じ本文も再判定し、以前の通過本文へ戻っても再判定する`, async ($, on) => {
    let text = JAPANESE;
    let calls = 0;
    on("session.id", () => ({ value: "language-test" }));
    on("session.messages", () => ({ value: [{ role: "assistant", text, toolUses: [] }] }));
    on("process.run", () => {
      calls++;
      const rejected = text === ENGLISH;
      return { value: { exitCode: rejected && failed === "process-failure" ? 1 : 0, stdout: JSON.stringify({ outcome: rejected ? "warn" : "pass", body: rejected ? "英語主体の本文。" : null, fix: "日本語で書き直す。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    text = ENGLISH;
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toBeDefined();
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toBeDefined();
    text = JAPANESE;
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    expect(calls).toBe(4);
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    expect(calls).toBe(4);
  });
}

test("本文取得失敗の後は同じ通過本文も再判定する", async ($, on) => {
  let retrievalFails = false;
  let calls = 0;
  on("session.id", () => ({ value: "language-test" }));
  on("session.messages", () => {
    if (retrievalFails) throw new Error("本文取得の失敗");
    return { value: [{ role: "assistant", text: JAPANESE, toolUses: [] }] };
  });
  on("process.run", () => {
    calls++;
    return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
  });
  on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
  retrievalFails = true;
  const denied = (await $.tool.call({ tool: "Bash", command: "ls" })).deny;
  expect(denied).toMatch(BLOCK_ENVELOPE);
  expect(denied).toContain("同じ呼び出しを繰り返さず");
  retrievalFails = false;
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
  expect(calls).toBe(2);
});

for (const [name, response, expected] of [
  ["非0終了", { exitCode: 7, stdout: "", stderr: "", isStdoutTruncated: false, isStderrTruncated: false }, "終了コード 7"],
  ["JSON破損", { exitCode: 0, stdout: "broken-json", stderr: "", isStdoutTruncated: false, isStderrTruncated: false }, "JSONとして不正"],
] as const) {
  test(`実行前判定の${name}は機械生成の遮断として診断と次の操作を返す`, async ($, on) => {
    on("session.id", () => ({ value: "language-test" }));
    on("session.messages", () => ({ value: [{ role: "assistant", text: JAPANESE, toolUses: [] }] }));
    on("process.run", () => ({ value: response }));
    on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
    const denied = (await $.tool.call({ tool: "Bash", command: "ls" })).deny;
    expect(denied).toMatch(BLOCK_ENVELOPE);
    expect(denied).toContain(expected);
    expect(denied).toContain("同じ呼び出しを繰り返さず");
    expect(denied).toContain("ユーザーへ報告する");
  });
}
