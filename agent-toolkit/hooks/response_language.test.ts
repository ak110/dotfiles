type ApiMessage = { role: "assistant"; content: ({ type: "text"; text: string } | { type: "tool_use"; id: string; name: string; input: Record<string, string> })[] };
import { expect, mock, test } from "claude-code/testing";

// 入力の形はClaude Code 2.1.304のAPI形式Message・ContentBlockに従う。
const ENGLISH = "The investigation is complete and the results have been verified.";
const JAPANESE = "調査と検証が完了したので、結果を確認する。";
const BLOCK_ENVELOPE = /^<atk-auto source="response-language" kind="block">\n[\s\S]*\n<\/atk-auto>$/;

for (const previousEnglish of [false, true]) {
  test(`合成API失敗行を除いたAPI形式で後続操作を判定する（先行英語=${previousEnglish}）`, async ($, on) => {
    on("session.id", () => ({ value: "synthetic-test" }));
    on("session.messages", (_$, e) => ({ value: e.as === "api"
      ? [...(previousEnglish ? [{ role: "assistant" as const, content: [{ type: "text" as const, text: ENGLISH }] }] : []),
          { role: "assistant" as const, content: [{ type: "tool_use" as const, id: "t1", name: "Bash", input: { command: "ls" } }] }]
      : [{ role: "assistant" as const, text: "Credit balance is too low", toolUses: [] }] }));
    on("process.run", (_$, e) => {
      expect(e.argv.at(-1)).toBe(`--text=${ENGLISH}`);
      return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: "warn", body: "英語主体の本文。", fix: "説明を日本語で書く。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
    const result = await $.tool.call({ tool: "Bash", command: "ls" });
    if (previousEnglish) expect(result.deny).toBeDefined();
    else expect(result.result).toBe("実行済み");
  });
}

for (const delegated of [false, true]) {
  test(`現在の本文への書き直しと本文無し再発行を判定する（委譲先=${delegated}）`, async ($, on) => {
    mock.env(on, delegated ? { AGENT_TOOLKIT_DELEGATED_SESSION: "1" } : {});
    on("session.id", () => ({ value: "language-test" }));
    let messages: ApiMessage[] = [{ role: "assistant", content: [{ type: "text", text: ENGLISH }] }];
    const checked: string[] = [];
    on("session.messages", (_$, e) => { expect(e.as).toBe("api"); return { value: messages }; });
    on("process.run", (_$, e) => {
      const text = e.argv.at(-1)!.slice("--text=".length);
      checked.push(text);
      return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: text === ENGLISH ? "warn" : "pass", body: "英語主体の本文。", fix: "日本語で書き直し、同じ呼び出しを再発行する。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toContain("日本語で書き直し");
    messages.push({ role: "assistant", content: [{ type: "tool_use", id: "toolu_1", name: "Bash", input: { command: "ls" } }] });
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toBeDefined();
    // 保存ファイルの英語が残っていても、実行中の新しい日本語だけを判定する。
    messages.push({ role: "assistant", content: [{ type: "text", text: JAPANESE }] });
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
    messages.push({ role: "assistant", content: [{ type: "text", text: ENGLISH }] });
    expect((await $.tool.call({ tool: "Bash", command: "ls" })).deny).toBeDefined();
    expect(checked).toEqual([ENGLISH, ENGLISH, JAPANESE, ENGLISH]);
  });
}

test("send_to_userで書き直した最新本文を後続操作が引き継ぐ", async ($, on) => {
  mock.env(on, {});
  on("session.id", () => ({ value: "language-test" }));
  const messages: ApiMessage[] = [
    { role: "assistant", content: [{ type: "text", text: ENGLISH }] },
    { role: "assistant", content: [{ type: "tool_use", id: "toolu_1", name: "mcp__agent-toolkit__send_to_user", input: { message: JAPANESE } }] },
    { role: "assistant", content: [{ type: "tool_use", id: "toolu_2", name: "Bash", input: { command: "ls" } }] },
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
    on("session.messages", () => ({ value: [{ role: "assistant", content: [{ type: "text", text }] }] }));
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

test("warnの後は同じ本文も再判定し、以前の通過本文へ戻っても再判定する", async ($, on) => {
  let text = JAPANESE;
  let calls = 0;
  on("session.id", () => ({ value: "language-test" }));
  on("session.messages", () => ({ value: [{ role: "assistant", content: [{ type: "text", text }] }] }));
  on("process.run", () => {
    calls++;
    const rejected = text === ENGLISH;
    return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: rejected ? "warn" : "pass", body: rejected ? "英語主体の本文。" : null, fix: "日本語で書き直す。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
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

// 判定基盤の障害は日本語へ書き直しても解消しないため、拒否すると後続の操作も報告も止まる。
// 障害の形（入力取得・プロセス実行・応答解析）ごとに、元の操作が1回だけ実行され、診断が同じ会話へ1回だけ届くことを確かめる。
const OK_RESPONSE = { exitCode: 0, stdout: JSON.stringify({ outcome: "pass", body: null, fix: "" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false };
const SECRET_STDERR = "判定プロセスの標準エラー";
const UNAVAILABLE: [string, { messages?: true; sessionId?: true; run?: true; response?: typeof OK_RESPONSE }, string][] = [
  ["本文取得の例外", { messages: true }, "応答本文の取得で例外が発生"],
  ["セッションID取得の例外", { sessionId: true }, "セッションIDの取得で例外が発生"],
  ["process.runの例外", { run: true }, "process.runで例外が発生"],
  ["非0終了", { response: { ...OK_RESPONSE, exitCode: 7, stdout: "", stderr: SECRET_STDERR } }, "終了コード 7"],
  ["標準出力の切り詰め", { response: { ...OK_RESPONSE, isStdoutTruncated: true } }, "標準出力の切り詰め true"],
  ["JSON破損", { response: { ...OK_RESPONSE, stdout: "broken-json" } }, "JSONとして不正"],
  ["JSON契約外", { response: { ...OK_RESPONSE, stdout: "{}" } }, "outcome・body・fixが契約外"],
];

for (const [name, failure, expected] of UNAVAILABLE) {
  test(`実行前判定の${name}では後続の操作を続け、診断を同じ会話へ1回だけ届ける`, async ($, on) => {
    let failing = true;
    let executed = 0;
    on("session.id", () => {
      if (failing && failure.sessionId) throw new Error("セッションIDの取得の失敗");
      return { value: "language-test" };
    });
    on("session.messages", () => {
      if (failing && failure.messages) throw new Error("本文取得の失敗");
      return { value: [{ role: "assistant", content: [{ type: "text", text: JAPANESE }] }] };
    });
    on("process.run", () => {
      if (failing && failure.run) throw new Error("判定処理の障害");
      return { value: failing && failure.response ? failure.response : OK_RESPONSE };
    });
    on("tool.call", { tool: "Bash" }, () => { executed++; return { result: "実行済み" }; });
    on("tool.call", { tool: "Read" }, () => ({ result: "読込済み" }));
    const first = await $.tool.call({ tool: "Bash", command: "ls" });
    expect(first.deny).toBeUndefined();
    expect(first.result).toBe("実行済み");
    expect(first.context?.length).toBe(1);
    const notice = first.context?.[0] ?? "";
    expect(notice).toMatch(/^<atk-auto source="response-language" kind="warn">\n[\s\S]*\n<\/atk-auto>$/);
    expect(notice).toContain(expected);
    expect(notice).toContain("ユーザーへ報告する");
    expect(notice).not.toContain(JAPANESE);
    expect(notice).not.toContain(SECRET_STDERR);
    expect(notice).not.toContain("broken-json");
    const second = await $.tool.call({ tool: "Read", file_path: "/repo/a.txt" });
    expect(second.result).toBe("読込済み");
    // 会話を識別できない障害では抑止しない。識別できる障害では同じ診断を重ねない。
    expect(second.context ?? []).toEqual(failure.messages || failure.sessionId ? [notice] : []);
    expect(executed).toBe(1);
    failing = false;
    const recovered = await $.tool.call({ tool: "Bash", command: "ls" });
    expect(recovered.result).toBe("実行済み");
    expect(recovered.context ?? []).toEqual([]);
    expect(executed).toBe(2);
  });
}

test("判定不能が解消して判定に成功した後の同じ障害は再び診断を届ける", async ($, on) => {
  let failing = true;
  let text = JAPANESE;
  on("session.id", () => ({ value: "language-test" }));
  on("session.messages", () => ({ value: [{ role: "assistant", content: [{ type: "text", text }] }] }));
  on("process.run", () => ({ value: failing ? { ...OK_RESPONSE, exitCode: 7 } : OK_RESPONSE }));
  on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).context?.length).toBe(1);
  failing = false;
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).context ?? []).toEqual([]);
  failing = true;
  text = "別の本文で次の操作を進める。";
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).context?.length).toBe(1);
});

test("判定不能の後の英語本文は正常な判定で書き直しを求める", async ($, on) => {
  let failing = true;
  let text = JAPANESE;
  on("session.id", () => ({ value: "language-test" }));
  on("session.messages", () => ({ value: [{ role: "assistant", content: [{ type: "text", text }] }] }));
  on("process.run", () => ({ value: failing ? { ...OK_RESPONSE, exitCode: 7 } : { ...OK_RESPONSE, stdout: JSON.stringify({ outcome: text === ENGLISH ? "warn" : "pass", body: "英語主体の本文。", fix: "日本語で書き直す。" }) } }));
  on("tool.call", { tool: "Bash" }, () => ({ result: "実行済み" }));
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
  failing = false;
  text = ENGLISH;
  const denied = await $.tool.call({ tool: "Bash", command: "ls" });
  expect(denied.deny).toMatch(BLOCK_ENVELOPE);
  expect(denied.deny).toContain("日本語で書き直す");
  text = JAPANESE;
  expect((await $.tool.call({ tool: "Bash", command: "ls" })).result).toBe("実行済み");
});
