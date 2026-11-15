import { expect, test } from "claude-code/testing";
import { awiStatusWarnings } from "./send_to_user.tsx";

for (const [message, warns] of [
  ["気付いた改善点: 対象。AWI化ステータス: 不要 被覆 既存の処置がある", false],
  ["気付いた改善点: 対象。AWI化ステータス: 不要 原因不明", true],
  ["気付いた改善点: 対象。AWI化ステータス: 予定 入力待ち 対象を確認する", false],
  ["```\n気付いた改善点: 対象。AWI化ステータス: 不要\n```", false],
] as const) {
  test(`新しい報告経路でも改善点の書式警告を保つ: ${message}`, () => {
    expect(awiStatusWarnings(message).length > 0).toBe(warns);
  });
}
