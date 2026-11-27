import { automatedMessage } from "./automated_message.ts";
import outputPolicy from "../agent_toolkit/_common/output-policy.ts";

export const recoveryRoute = "長い完了報告を書く前に `send_to_user_preflight({})` で前提を確認する。拒否後は通知の `body_ref` だけで保存本文を渡し、修正が必要な場合だけ本文の該当箇所を編集する。";

export const questionRoute = "回答を求める質問・選択は、ツール一覧に`AskUserQuestion`があればそれで尋ね、なければmessageで送る。";
export function deliveryGuidance(alreadyDelivered: boolean): string {
  return alreadyDelivered && outputPolicy.full_guidance_once ? outputPolicy.send_repeat : outputPolicy.send_guidance;
}

// `AWI化ステータス`の値ごとに、理由の先頭に置く区分名と、区分名で始まらない場合の警告を対応付ける。
// 書式と区分の定義元は`skills/atk-mode/playbooks/main-reporting.md`「進捗報告」にある。警告は区分を本文に挙げ、定義元を読まずに直せるようにする。
// `済`の時機と根拠、`即時対応`の意味判定は担当が行い、この書式検査では判定しない。
// 判定は区分名と冒頭の語句との文字列照合だけとし、理由の意味は推定しない。送達は止めない。
function statusCheck<const Value extends string, const Category extends string>(
  value: Value,
  categories: readonly Category[],
  form: string,
  detail = "",
) {
  const listed = categories.map((name) => `\`${name}\``).join("・");
  const warning = automatedMessage(
    "send-to-user",
    "warn",
    `本文は表示済みである。\`AWI化ステータス: ${value}\`の直後に区分名が無い行がある。` +
      `以後の該当行は\`AWI化ステータス: ${value} <区分> ${form}\`とし、区分は${listed}から選んでください。${detail}`,
  );
  return { value, categories, warning };
}

const STATUS_CHECKS = [
  statusCheck(
    "不要",
    ["非欠陥", "処置済み", "被覆", "所有範囲外", "見送り"],
    "<理由>",
    "`見送り`の理由には`作り込み側の候補:`・`見逃し側の候補:`・`比較結論:`をセミコロンで区切って書いてください。",
  ),
  statusCheck("予定", ["判断待ち", "認可待ち", "入力待ち", "書込競合"], "<再開条件>"),
] as const;
// `不要`の理由（区分名の後）の冒頭が、原因を調べていないことを見送りの根拠にしている形。「未調査を理由に見送らない」のように助詞が続く説明は除く。
// 「原因は未確定だが、既存AWIへ引き継ぎ済み」のように逆接が続く形は、後段の処置・担当を理由にしているため除く。
// Stopの直接応答でも同じ判定を行う（`agent_toolkit/_hooks/improvement_status_advisor.py`の`UNINVESTIGATED_REASON`）。判定語を変える場合は両方を合わせる。
const UNINVESTIGATED_REASON = /^(?:原因(?:が|は)?(?:まだ)?(?:未調査|未確定|未特定|不明)|原因を(?:まだ)?(?:調べて|調査して|特定して)いない|未調査(?![をにと]))(?!(?:だ|です)?(?:が|けれど|けど)|ながら|ものの|でも)/;
const UNINVESTIGATED_WARNING = automatedMessage(
  "send-to-user",
  "warn",
  "`AWI化ステータス: 不要`の理由が、原因を調べていないことを見送りの根拠にしている箇所がある。本文は表示済みである。" +
    "担当と処置を決めるために必要な範囲を調べ、該当する理由を調査・処置の結果に基づいて更新してください。",
);
const STATUS_MARKER = /AWI化ステータス:\s*(不要|予定)[ \t]*/g;
const DEFERRAL_FIELDS = ["作り込み側の候補", "見逃し側の候補", "比較結論"] as const;
const DEFERRAL_FIELD = /(作り込み側の候補|見逃し側の候補|比較結論)\s*[:：]\s*([^;；\n]*)/g;

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

export function reportWarnings(message: string, deliveryId?: string, skipped: readonly string[] = []): string[] {
  const warnings = new Set<string>();
  function warn(check: string, warning: string): void {
    if (skipped.includes(check)) return;
    if (deliveryId === undefined) {
      warnings.add(warning);
      return;
    }
    const claim = JSON.stringify([{ check, reason: "誤検出と判断した理由", delivery_id: deliveryId }]);
    const guidance =
      `次の操作: 誤検出なら表示済みの本文は再掲せず、次のsend_to_userに \`misdetection=${claim}\` を付けてください。` +
      "同じ送達に誤検出が複数あれば、それぞれの判定名と送達IDの組を1つのmisdetection配列にまとめてください。" +
      '次が質問だけなら、質問の前に `message: "…"` とその申告を付けたsend_to_userを1回呼んでください。';
    warnings.add(warning.replace('kind="warn">', `kind="warn" check="${check}" delivery_id="${deliveryId}">`)
      .replace("</atk-auto>", `${guidance}\n</atk-auto>`));
  }
  const clockClaims = reportBodyLines(message).map((line) => line.replace(/`[^`\n]+`/g, ""));
  if (clockClaims.some((line) => /(?:^|[^0-9A-Za-z_])(?:(?:[01]?\d|2[0-3]):(?:[0-5][xX]|[xX][0-9xX])|(?:[012][xX]|[xX][0-9xX]):(?:[0-5][0-9xX]|[xX][0-9xX]))(?![0-9A-Za-z_])/.test(line))) {
    warn("clock-claim", automatedMessage("send-to-user", "warn",
      "本文は表示済みである。時刻の一部が伏せ字になっている。観測値か、推定と分かる時間帯を示してください。"));
  }
  for (const line of reportedLines(message)) {
    for (const matched of line.matchAll(STATUS_MARKER)) {
      const check = STATUS_CHECKS.find((candidate) => candidate.value === matched[1])!;
      const rest = line.slice((matched.index ?? 0) + matched[0].length);
      const category = check.categories.find((name) => rest.startsWith(name));
      if (category === undefined) warn(check.value === "不要" ? "status-unnecessary-kind" : "status-planned-kind", check.warning);
      const reason = rest.slice(category?.length ?? 0).replace(/^[ \t、,]+/, "");
      if (check.value === "不要" && UNINVESTIGATED_REASON.test(reason)) warn("uninvestigated-reason", UNINVESTIGATED_WARNING);
      if (check.value === "不要" && category === "見送り") {
        const report = rest.split("AWI化ステータス:", 1)[0]!;
        const values = new Map([...report.matchAll(DEFERRAL_FIELD)].map((field) => [field[1], field[2]!.replace(/[\s。）」)]+$/g, "")]));
        const missing = DEFERRAL_FIELDS.filter((name) => !values.get(name));
        if (missing.length > 0) warn("deferral-comparison", automatedMessage(
          "send-to-user", "warn",
          `見送り報告の比較項目が空または欠落している: ${missing.join("、")}。` +
            "同じ報告行に`作り込み側の候補:`・`見逃し側の候補:`・`比較結論:`を、セミコロンで区切って記録してください。",
        ));
      }
    }
  }
  return [...warnings];
}
