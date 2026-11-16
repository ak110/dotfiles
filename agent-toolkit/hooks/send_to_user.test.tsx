import { expect, test } from "claude-code/testing";
import { awiStatusWarnings } from "./send_to_user.tsx";

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
    expect(awiStatusWarnings(message).length > 0).toBe(warns);
  });
}
