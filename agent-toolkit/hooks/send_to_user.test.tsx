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
      expect(shown.description).toContain("`…`だけ");
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
    for (const text of ["最初の依頼", "続き", '<atk-auto source="fixture" kind="notice">機械入力</atk-auto>']) {
      const previous = text === "続き" ? ["既存の案内"] : undefined;
      await $.prompt.submit({ text, context: previous, wait: false, origin: { kind: "composer" } });
      const submitted = received.shift();
      expect(submitted?.text).toBe(text);
      const context = submitted?.context ?? [];
      if (shouldDeliver && !text.startsWith("<atk-auto")) {
        expect(context.slice(0, -1)).toEqual(previous ?? []);
        const notice = context.at(-1) ?? "";
        expect(notice).toMatch(/着手.*進捗.*完了報告.*send_to_user/);
        expect(notice).toMatch(/質問.*AskUserQuestion/);
        if (registered) expect(notice).toContain(`await tools['${tool}']({message})`);
        expect(notice).toContain("呼出しに失敗した時だけ通常本文");
        expect(notice).toContain("searchTools");
        expect(notice).not.toContain("ツールが無い時");
        expect(notice).toContain("…");
        expect(notice.length).toBeLessThanOrEqual(300);
      } else {
        expect(context).toEqual(previous ?? []);
      }
    }
  });
}

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

// 固定の報告ごとに、送達後に欠けた記録の行頭語を期待する。
for (const [message, heads] of [
  ["作業中に自分の手順漏れを見つけたため、漏れた手順をその場で実施した。", ["処置"]],
  ["引数の誤りを直し、再実行で成功した。", ["処置"]],
  ["- 期待: 対象の全件を処理する。\n- 原因: 範囲の指定の誤りで末尾の1件が漏れていた。\n- 確認: 再実行で全件の処理を確かめた。", ["処置"]],
  ["統合の後の点検で古い版を使う誤りがあった。\n原因: 統合で受け取った版を後の判断や点検へ引き継ぐ仕組みが無い。\n処置: 引き継ぐ手順を足す。", []],
  ["設定の値は勝手に変えていない。現行の値と作業記録の一部を確かめた。", []],
  ["ユーザーの指摘で手順漏れが分かった。\n処置: 漏れた手順を実施し、残る影響が無いことを確かめた。", ["再発防止"]],
  ["> 作業中に自分の手順漏れを見つけた。\n```text\n誤りを直した。\n```", []],
  ["実装を終え、変更範囲の試験が通った。", []],
  ["変更範囲の試験を実行し、失敗0件で誤りは無いことを確かめた。", []],
  ["レビューで指摘が3件あり、対応している。CIではpytestが2件失敗した。考慮漏れの有無は次の工程で確かめる。", []],
  // 以下は一人称で逸脱を述べた実際の報告の形と、仮定・問い・否定の形。
  ["原稿の件数とファイル数を誤って伝えていた。", ["処置"]],
  ["誤って作業ツリーのファイルを削除した。", ["処置"]],
  ["起票の前に所有範囲を判定していなかったことが根本の誤りで、費用の比較はその次だった。", ["処置"]],
  ["**統括の作業ミス**\n割当ファイルを作るスクリプトを統合先worktreeのcwdで実行したため、別のworktreeに書いた。", ["処置"]],
  ["前回の誤り: 確かめたのは現行のコードだけだった。", ["処置"]],
  ["判断の誤り: 担当の提案を選んだ。", ["処置"]],
  ["記録の対応付けを誤り、応答の約1%しか拾っていなかった。", ["処置"]],
  ["隔離HOMEへ共有RUSTUP_HOMEが継承される不備を検出した。", ["処置"]],
  ["全OSの子孫再走査へ入り、性能退行を確認した。", ["処置"]],
  ["確認を忘れた。", ["処置"]],
  ["失敗していた試験を直した。", ["処置"]],
  ["自分の対応で同じ失敗を繰り返していた。", ["処置", "再発防止"]],
  ["登録し直しを忘れたまま判定する操作は、CLIが受け付けない。", []],
  ["記録を忘れた場合の扱いを先に洗い出す。", []],
  ["やり方のどこが誤っているかを調べる。", []],
  ["設定は誤っていない。", []],
] as const) {
  test(`逸脱の報告に欠けた記録の行だけを警告する: ${message}`, () => {
    const warnings = reportWarnings(message);
    for (const head of ["処置", "再発防止"]) {
      expect(warnings.some((warning) => warning.includes(`\`${head}:\`の行が無い`))).toBe((heads as readonly string[]).includes(head));
    }
  });
}
