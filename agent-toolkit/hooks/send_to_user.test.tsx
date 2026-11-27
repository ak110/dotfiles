import { expect, mock, test } from "claude-code/testing";
import { reportWarnings } from "./send_to_user.tsx";

for (const [surface, delegated, tool, shouldDeliver] of [
  ["terminal", false, "mcp__plugin_agent-toolkit_agents_server__send_to_user", true],
  ["terminal", true, "mcp__plugin_agent-toolkit_agents_server__send_to_user", false],
  ["desktop", false, "mcp__plugin_agent-toolkit_agents_server__send_to_user", false],
  [null, false, "mcp__plugin_agent-toolkit_agents_server__send_to_user", false],
  ["terminal", false, "mcp__other__send_to_user", false],
] as const) {
  test(`初回のツール一覧で発話の送り先を示す条件: ${surface}/${delegated}/${tool}`, async ($, on) => {
    mock.env(on, { AGENT_TOOLKIT_DELEGATED_SESSION: delegated ? "1" : "" });
    on("session.surfaces", () => ({ value: surface ? [surface] : [] }));
    on("tool.describe", (_$, e) => ({ description: e.description, isDeferred: true }));
    const original = { description: "表示に成功した時だけ確認を返す。", isDeferred: true } as const;
    const shown = await $.tool.describe({ tool, ...original, provider: { plugin: "mcp:agents_server", tier: "user" } });
    if (shouldDeliver) {
      expect(shown.isDeferred).toBe(false);
      for (const purpose of ["メイン", "進捗", "完了報告", "前置き"]) expect(shown.description).toContain(purpose);
      expect(shown.description).toMatch(/質問[^。]*ツール一覧[^。]*AskUserQuestion/);
      expect(shown.description).not.toContain("送信済み");
      expect(shown.description).toContain(original.description);
    } else {
      expect(shown).toEqual(original);
    }
  });
}

for (const [surface, delegated, owner, registered, shouldDeliver] of [
  ["terminal", "", "", true, true],
  ["terminal", "", "", false, true],
  ["terminal", "1", "", true, false],
  ["terminal", "", "parent", true, false],
  ["desktop", "", "", true, false],
  [null, "", "", true, false],
] as const) {
  test(`入力ごとの送り先は端末のメインだけに届く: ${surface}/${delegated}/${owner}/${registered}`, async ($, on) => {
    mock.env(on, { AGENT_TOOLKIT_DELEGATED_SESSION: delegated, AGENT_TOOLKIT_OWNER_SESSION: owner });
    on("session.surfaces", () => ({ value: surface ? [surface] : [] }));
    const tool = "mcp__plugin_agent-toolkit_agents_server__send_to_user";
    on("tool.list", () => ({ value: registered ? [{ name: tool, description: "表示", mcp: true }] : [] }));
    const received: { text: string; context?: readonly string[] }[] = [];
    on("prompt.submit", (_$, e) => { received.push(e); return { text: e.text }; });
    const goal = '<atk-auto source="process-loop" kind="goal">\n起動目標\n</atk-auto>';
    const runSkillGoal = '<atk-auto source="run-skill" kind="goal">\n起動目標\n</atk-auto>';
    const goals = [goal, runSkillGoal];
    let delivered = false;
    for (const [text, origin] of [
      ["最初の依頼", "composer"],
      ["続き", "composer"],
      ['<atk-auto source="fixture" kind="notice">機械入力</atk-auto>', "composer"],
      ['<atk-auto source="fixture" data-kind="goal">機械入力</atk-auto>', "composer"],
      ["外部からの入力", "unclassified"],
      [goal, "composer"],
      [runSkillGoal, "unclassified"],
    ] as const) {
      const previous = text === "続き" ? ["既存の案内"] : undefined;
      // 同じ文脈では最初に届く発話にだけ案内を添える（同じ文面の繰り返しは読み飛ばされる）。 pyfltr: allow-comment
      const first = !delivered;
      await $.prompt.submit({ text, context: previous, wait: false, origin: { kind: origin } });
      const submitted = received.shift();
      expect(submitted?.text).toBe(text);
      const context = submitted?.context ?? [];
      if (shouldDeliver && first && (goals.includes(text) || (origin === "composer" && !text.startsWith("<atk-auto")))) {
        delivered = true;
        expect(context.slice(0, -1)).toEqual(previous ?? []);
        const notice = context.at(-1) ?? "";
        expect(notice).toMatch(/着手.*進捗.*完了報告.*send_to_user/);
        expect(notice).toMatch(/質問.*AskUserQuestion/);
        expect(notice).toContain(tool);
        if (registered) expect(notice).toContain(`await tools['${tool}']({message})`);
        expect(notice).toContain(registered ? 'source="send-to-user-contract"' : 'source="send-to-user-search"');
        expect(notice).toContain("searchTools");
        expect(notice).not.toContain("ツールが無い時");
        expect(notice).not.toContain("送信済み");
        expect(notice.length).toBeLessThanOrEqual(300);
      } else {
        expect(context).toEqual(previous ?? []);
      }
    }
  });
}

test("送り先の案内は会話の圧縮の後に再び1回届く", async ($, on) => {
  mock.env(on, { AGENT_TOOLKIT_DELEGATED_SESSION: "", AGENT_TOOLKIT_OWNER_SESSION: "" });
  on("session.surfaces", () => ({ value: ["terminal"] }));
  const tool = "mcp__plugin_agent-toolkit_agents_server__send_to_user";
  on("tool.list", () => ({ value: [{ name: tool, description: "表示", mcp: true }] }));
  const received: { context?: readonly string[] }[] = [];
  on("prompt.submit", (_$, e) => { received.push(e); return { text: e.text }; });
  on("session.id", () => ({ value: "compact-notice" }));
  const summary = [{ role: "user" as const, text: "要約", toolUses: [] }];
  on("session.compact", () => ({ messages: summary }));
  const notified = async (text: string) => {
    await $.prompt.submit({ text, wait: false, origin: { kind: "composer" } });
    return (received.shift()?.context ?? []).some((item) => item.includes('source="send-to-user-contract"'));
  };
  expect(await notified("最初の依頼")).toBe(true);
  expect(await notified("続き")).toBe(false);
  await $.session.compact({ trigger: "auto", messages: summary });
  expect(await notified("圧縮後の依頼")).toBe(true);
  expect(await notified("さらに続き")).toBe(false);
});

for (const [message, warns] of [
  ["気付いた改善点: 対象。AWI化ステータス: 不要 被覆 既存の処置がある", false],
  ["気付いた改善点: 対象。AWI化ステータス: 不要 原因不明", true],
  ["気付いた改善点: 対象。AWI化ステータス: 予定 入力待ち 対象を確認する", false],
  ["```\n気付いた改善点: 対象。AWI化ステータス: 不要\n```", false],
  ["気付いた改善点: 対象。AWI化ステータス: 不要 見送り", true],
  ["気付いた改善点: 対象。AWI化ステータス: 不要 見送り 作り込み側の候補: 統合; 見逃し側の候補: 試行; 比較結論: 継続費用が損失を上回る", false],
  ["気付いた改善点: 対象。AWI化ステータス: 不要 見送り 作り込み側の候補: 統合; 見逃し側の候補: ; 比較結論: 継続費用が損失を上回る", true],
  ["> 気付いた改善点: 対象。AWI化ステータス: 不要 見送り", false],
  ["気付いた改善点: A。AWI化ステータス: 不要 見送り 作り込み側の候補: 統合; 見逃し側の候補: 試行; 比較結論: 継続費用が損失を上回る。AWI化ステータス: 不要 見送り", true],
  ["気付いた改善点: A。AWI化ステータス: 不要 見送り 作り込み側の候補: 統合; 見逃し側の候補: 試行\n気付いた改善点: B。AWI化ステータス: 不要 見送り 比較結論: 継続費用が損失を上回る", true],
] as const) {
  test(`新しい報告経路でも改善点の書式警告を保つ: ${message}`, () => {
    expect(reportWarnings(message).length > 0).toBe(warns);
  });
}
