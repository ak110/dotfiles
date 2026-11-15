import { automatedMessage } from "./automated_message.ts";

// `AWI化ステータス`の値ごとに、理由の先頭に置く区分名と、区分名で始まらない場合の警告を対応付ける。
// 書式と区分の定義元は`share/rules/rules-main.md`「進捗報告」のテンプレート直下にある。
// `済`の時機と根拠、`即時対応`の意味判定は担当が行い、この書式検査では判定しない。
// 判定は区分名と冒頭の語句との文字列照合だけとし、理由の意味は推定しない。送達は止めない。
const STATUS_CHECKS = [
  {
    value: "不要",
    categories: ["非欠陥", "処置済み", "被覆", "見送り"],
    warning: automatedMessage(
      "send-to-user",
      "warn",
      "本文は表示済みである。`AWI化ステータス: 不要`の直後に区分名が無い行がある。" +
        "`share/rules/rules-main.md`「進捗報告」のテンプレート直下の書式に従い、以後の該当行に区分名と理由を書け。書式の修正だけなら判定資料の追加取得は不要である。",
    ),
  },
  {
    value: "予定",
    categories: ["判断待ち", "認可待ち", "入力待ち", "書込競合"],
    warning: automatedMessage(
      "send-to-user",
      "warn",
      "本文は表示済みである。`AWI化ステータス: 予定`の直後に区分名が無い行がある。" +
        "`share/rules/rules-main.md`「進捗報告」のテンプレート直下の書式に従い、以後の該当行に区分名と再開条件を書け。書式の修正だけなら判定資料の追加取得は不要である。",
    ),
  },
] as const;
// `不要`の理由（区分名の後）の冒頭が、原因を調べていないことを見送りの根拠にしている形。「未調査を理由に見送らない」のように助詞が続く説明は除く。
// 「原因は未確定だが、既存AWIへ引き継ぎ済み」のように逆接が続く形は、後段の処置・担当を理由にしているため除く。
// Stopの直接応答でも同じ判定を行う（`agent_toolkit/_hooks/improvement_status_advisor.py`の`UNINVESTIGATED_REASON`）。判定語を変える場合は両方を合わせる。
const UNINVESTIGATED_REASON = /^(?:原因(?:が|は)?(?:まだ)?(?:未調査|未確定|未特定|不明)|原因を(?:まだ)?(?:調べて|調査して|特定して)いない|未調査(?![をにと]))(?!(?:だ|です)?(?:が|けれど|けど)|ながら|ものの|でも)/;
const UNINVESTIGATED_WARNING = automatedMessage(
  "send-to-user",
  "warn",
  "`AWI化ステータス: 不要`の理由が、原因を調べていないことを見送りの根拠にしている箇所がある。本文は表示済みである。" +
    "`share/rules/rules-main.md`「作業中の改善点の報告」に従い、該当する理由を調査・処置の結果に基づいて更新せよ。",
);
const STATUS_MARKER = /AWI化ステータス:\s*(不要|予定)[ \t]*/g;

// 気付いた改善点の抽出はPythonのruntime_inserted.reported_improvement_linesと同じ境界にする。
// 送達ツールでは、30分点検の「改善案:」に続く項目も報告として受け取る。
function reportedLines(message: string): string[] {
  const lines: string[] = [];
  let fence: string | undefined;
  let improvementList = false;
  const text = message.replace(/<(atk-auto|agent-toolkit-auto-inserted|agent-toolkit-hook-message)(?:\s[^>]*)?>[\s\S]*?<\/\1>/g, "");
  for (const line of text.split("\n")) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line);
    if (marker) {
      const run = marker[1]!;
      if (fence === undefined) fence = run;
      else if (run[0] === fence[0] && run.length >= fence.length) fence = undefined;
      improvementList = false;
      continue;
    }
    if (fence !== undefined) continue;
    if (/^ {0,3}改善案:[ \t]*$/.test(line)) {
      improvementList = true;
      continue;
    }
    if (/^ {0,3}(?:(?:[-*+]|\d+[.)])[ \t]+)?気付いた改善点:/.test(line)) {
      lines.push(line);
      continue;
    }
    if (improvementList && /^ {0,3}(?:[-*+]|\d+[.)])[ \t]+/.test(line)) lines.push(line);
    else if (line.trim()) improvementList = false;
  }
  return lines;
}

// 区分名で始まらない値と、未調査を理由にした`不要`の警告を、重複を除いて返す。
export function awiStatusWarnings(message: string): string[] {
  const warnings = new Set<string>();
  for (const line of reportedLines(message)) {
    for (const matched of line.matchAll(STATUS_MARKER)) {
      const check = STATUS_CHECKS.find((candidate) => candidate.value === matched[1])!;
      const rest = line.slice((matched.index ?? 0) + matched[0].length);
      const category = check.categories.find((name) => rest.startsWith(name));
      if (category === undefined) warnings.add(check.warning);
      const reason = rest.slice(category?.length ?? 0).replace(/^[ \t、,]+/, "");
      if (check.value === "不要" && UNINVESTIGATED_REASON.test(reason)) warnings.add(UNINVESTIGATED_WARNING);
    }
  }
  return [...warnings];
}
