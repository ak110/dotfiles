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
const DEFERRAL_FIELDS = ["作り込み側の候補", "見逃し側の候補", "比較結論"] as const;
const DEFERRAL_FIELD = /(作り込み側の候補|見逃し側の候補|比較結論)\s*[:：]\s*([^;；\n]*)/g;

// 判定語の直後が否定の場合を除く先読みと、報告の形の手掛かりごとの判定語。語の一致で案内の候補を選ぶだけで、意味は推定しない。
// 定義元はPythonの`agent_toolkit/_common/runtime_inserted.py`の`CUE_NEGATION`・`REPORT_CUES`で、送達後の検査に使う種類だけを同じ文字列で持つ。
// 判定語を変える場合は両方を合わせる。一致は`agent_toolkit/_hooks/improvement_status_advisor_test.py`が確かめる。
const CUE_NEGATION = "(?!(?:は|が|も|では)?(?:無|な)(?:い|く|し|かっ)|(?:は|が|も)?見つから|(?:し|て|して)?い?(?:な(?:い|く|かっ)|ませ)|せず|(?:は|が)?[0０]件)";
const REPORT_CUES = {
  deviation: "(?:バグ|不具合|(?<!非)欠陥|不備|不整合|誤り|誤っ|間違(?:い|え|っ)|ミス|漏れ|見落と|取り違|失念|忘れ(?:てい|た)|抜け(?:てい|落ち)|逸脱|退行|壊れ|壊し|失敗(?!例|時|した場合|する場合)|エラー(?:が(?:出|発生|起き|残)|にな)|指摘|再発(?!防止))",
  prevention: "(?:ユーザー(?:から|に|が)?の?再?指摘|再指摘|再発(?!防止)|同じ(?:誤り|失敗|ミス|不具合|症状|問題|害|指摘)|(?:2|２|二)(?:回|度)目の(?:修正|失敗|指摘)|破損|消失|漏洩|漏えい|依頼の未達)",
} as const;
type Cue = keyof typeof REPORT_CUES;
const CUE_PATTERNS = (Object.keys(REPORT_CUES) as Cue[]).map((cue) => [cue, new RegExp(REPORT_CUES[cue] + CUE_NEGATION)] as const);
// 送達後に、手掛かりの組に対して求める記録の行頭語（`agent-basics:bugfix`の「記録」）。
// Stopの`improvement_status_advisor.py`の`RECORD_LINE_CHECKS`と同じ組にする。bugfixの起動の有無は条件にしない。
const RECORD_LINE_CHECKS: readonly { cues: readonly Cue[]; head: string; warning: string }[] = [
  {
    cues: ["deviation"],
    head: "処置",
    warning: automatedMessage(
      "send-to-user",
      "warn",
      "本文は表示済みである。逸脱を示す語を含む報告に`処置:`の行が無い。" +
        "以後の報告では、今回の是正と残る影響を`処置:`の行に書け。bugfixの起動の記録は、今回の事象へ5つの問いを適用したことを示さない。逸脱の報告に当たるか、行の内容が足りるかは担当が判断せよ。",
    ),
  },
  {
    cues: ["deviation", "prevention"],
    head: "再発防止",
    warning: automatedMessage(
      "send-to-user",
      "warn",
      "本文は表示済みである。再発防止を検討する条件（ユーザーの指摘、同じ害の繰り返し、大きな害）を示す語を含む逸脱の報告に`再発防止:`の行が無い。" +
        "以後の報告では、再発防止の採否を`再発防止:`の行に書け。採らない場合は区分名と理由を書く。条件に当たるか、採否と内容は担当が判断せよ。",
    ),
  },
];

// 自動挿入の要素とフェンス内を除いた行。Pythonの`runtime_inserted.report_lines`と同じ境界にし、フェンスの境界はnullで返す。
function unfencedLines(message: string): (string | null)[] {
  const lines: (string | null)[] = [];
  let fence: string | undefined;
  const text = message.replace(/<(atk-auto|agent-toolkit-auto-inserted|agent-toolkit-hook-message)(?:\s[^>]*)?>[\s\S]*?<\/\1>/g, "");
  for (const line of text.split("\n")) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line);
    if (marker) {
      const run = marker[1]!;
      if (fence === undefined) fence = run;
      else if (run[0] === fence[0] && run.length >= fence.length) fence = undefined;
      lines.push(null);
    } else if (fence === undefined) lines.push(line);
  }
  return lines;
}

// 引用と字下げのコード例を除き、項目記号と字下げを外した報告の本文行。
function reportBodyLines(message: string): string[] {
  const lines: string[] = [];
  for (const line of unfencedLines(message)) {
    if (line === null || line.startsWith("\t") || line.trimStart().startsWith(">")) continue;
    if (!/^ {0,3}(?:[-*+]|\d+[.)])[ \t]/.test(line) && line.startsWith("    ")) continue;
    lines.push(line.replace(/^ {0,3}(?:(?:[-*+]|\d+[.)])[ \t]+)?/, "").trimEnd());
  }
  return lines;
}

// 気付いた改善点の行。送達ツールでは、30分点検の「改善案:」に続く項目も報告として受け取る。
function reportedLines(message: string): string[] {
  const lines: string[] = [];
  let improvementList = false;
  for (const line of unfencedLines(message)) {
    if (line === null) {
      improvementList = false;
      continue;
    }
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

// 区分名で始まらない値、未調査を理由にした`不要`、逸脱の報告に欠けた記録の行の警告を、重複を除いて返す。
export function reportWarnings(message: string): string[] {
  const warnings = new Set<string>();
  for (const line of reportedLines(message)) {
    for (const matched of line.matchAll(STATUS_MARKER)) {
      const check = STATUS_CHECKS.find((candidate) => candidate.value === matched[1])!;
      const rest = line.slice((matched.index ?? 0) + matched[0].length);
      const category = check.categories.find((name) => rest.startsWith(name));
      if (category === undefined) warnings.add(check.warning);
      const reason = rest.slice(category?.length ?? 0).replace(/^[ \t、,]+/, "");
      if (check.value === "不要" && UNINVESTIGATED_REASON.test(reason)) warnings.add(UNINVESTIGATED_WARNING);
      if (check.value === "不要" && category === "見送り") {
        const report = rest.split("AWI化ステータス:", 1)[0]!;
        const values = new Map([...report.matchAll(DEFERRAL_FIELD)].map((field) => [field[1], field[2]!.replace(/[\s。）」)]+$/g, "")]));
        const missing = DEFERRAL_FIELDS.filter((name) => !values.get(name));
        if (missing.length > 0) warnings.add(automatedMessage(
          "send-to-user", "warn",
          `見送り報告の比較項目が空または欠落している: ${missing.join("、")}。` +
            "同じ報告行に`作り込み側の候補:`・`見逃し側の候補:`・`比較結論:`を、セミコロンで区切って記録せよ。候補と結論の妥当性は担当が判断せよ。",
        ));
      }
    }
  }
  const body = reportBodyLines(message);
  const text = body.map((line) => line.replace(/`[^`\n]+`/g, "")).join("\n");
  const cues = new Set(CUE_PATTERNS.filter(([, pattern]) => pattern.test(text)).map(([cue]) => cue));
  const heads = new Set(body.map((line) => line.replaceAll("：", ":")).filter((line) => line.includes(":")).map((line) => line.split(":", 1)[0]));
  for (const check of RECORD_LINE_CHECKS) {
    if (check.cues.every((cue) => cues.has(cue)) && !heads.has(check.head)) warnings.add(check.warning);
  }
  return [...warnings];
}
