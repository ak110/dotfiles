import type { EngineInterface, On } from "claude-code";
import { automatedMessage } from "./automated_message.ts";
import { pluginLaunchCommand } from "./plugin_launch.ts";
import { deliverOnce, type LanguageCheck, languageCheckCommand, languageCheckResult, languageCheckUnavailable, withNotice } from "./response_language.ts";

// Claude Codeは、同じ応答でツール呼び出しより前に置いた地の文の一部を、APIが返す要約（progress update）へ置き換えて表示する。
// ツールの入力は要約されないため、ユーザーへ届ける本文をこのツールの`message`で運び、
// 呼び出しの行を`message`の`Markdown`で描く。ツールの登録と表示を同じmoduleに置き、表示できる環境にだけツールが現れるようにする。
// メイン用ツールであり、委譲先への登録と委譲先・サブエージェントからの呼び出しは`register.ts`が除外する。
// 設計と不採用とした代替は`docs/development/design-hooks-send-to-user.md`「send_to_userツール」にある。

const TOOL_NAME = "send_to_user";

const DESCRIPTION = [
  "ユーザーへ届ける本文を、途中・末尾ともにユーザーの画面へそのまま表示する。",
  "質問への回答、確認結果、判明した事実や原因、成果物、進捗、作業完了報告と次の工程の予告に使う。",
  "ユーザーの判断を求める確認には既存の質問手段を使う。推論は送らない。",
  "表示成功後は同じ本文を地の文へ再掲せず、残工程または通常のターン終了へ進む。",
].join("");

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    message: { type: "string", description: "ユーザーの画面へそのまま表示するMarkdownの本文。" },
  },
  required: ["message"],
  additionalProperties: false,
};

function fullName(plugin: string): string {
  return `mcp__${plugin}__${TOOL_NAME}`;
}

function messageOf(input: unknown): string | undefined {
  if (input && typeof input === "object" && "message" in input) {
    const message = (input as { message: unknown }).message;
    if (typeof message === "string") return message;
  }
  return undefined;
}

// `register.ts`の`session.start`が`$.tool.register`へ渡すツールの定義。
export const SEND_TO_USER_TOOL = { name: TOOL_NAME, description: DESCRIPTION, inputSchema: INPUT_SCHEMA, isDeferred: false };

// 委譲先からの呼び出しへ`register.ts`が返す拒否の本文。表示済みと応答すると委譲先が終了の返却を省くため、未表示であることと返却先を示す。
export const SEND_TO_USER_DELEGATED_DENY =
  "委譲先・サブエージェントからの呼び出しではユーザーの画面へ表示していない。結果本文は委譲元へ返す必要があるため、" +
  "このツールを使わず、報告する本文を全て最終応答（終了の返却）へ書け。";

// `AWI化ステータス`の値ごとに、理由の先頭に置く区分名と、区分名で始まらない場合の警告を対応付ける。
// 値と記入方法の定義元は`share/rules/rules-main.md`「作業中の改善点の報告」、`不要`の区分は`agent-basics:bugfix`の
// `references/root-cause-prevention.md`「再発防止策の必須性」、`予定`の区分は`agent-toolkit:delegation`の
// `references/waiting-and-monitoring.md`「待機中の先行実施」にある。`済`・`即時対応`は理由を持たないため検査しない。
// 判定は区分名と冒頭の語句との文字列照合だけとし、理由の意味は推定しない。送達は止めない。
const STATUS_CHECKS = [
  {
    value: "不要",
    categories: ["非欠陥", "処置済み", "被覆", "見送り"],
    warning: automatedMessage(
      "send-to-user",
      "warn",
      "`AWI化ステータス: 不要`の直後が区分名（非欠陥・処置済み・被覆・見送り）で始まらない箇所がある。" +
        "本文は表示済みである。`agent-basics:bugfix`の`references/root-cause-prevention.md`「再発防止策の必須性」で区分を判定し、" +
        "どの区分にも入らなければ再発防止策を検討して、以後の報告で区分名から理由を書け。",
    ),
  },
  {
    value: "予定",
    categories: ["判断待ち", "認可待ち", "入力待ち", "書込競合"],
    warning: automatedMessage(
      "send-to-user",
      "warn",
      "`AWI化ステータス: 予定`の直後が区分名（判断待ち・認可待ち・入力待ち・書込競合）で始まらない箇所がある。" +
        "本文は表示済みである。待機中に登録できる改善は、その場で既存項目と比較してAWIを保存せよ。" +
        "`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「待機中の先行実施」の区分に当たらなければ`予定`を使わず、" +
        "以後の報告で区分名と再開条件から書け。",
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
    "未調査は処置不要の根拠にならない。直接的原因と影響を確かめる最小調査をその場で行い、処置と担当を決めて報告を更新せよ。" +
    "担当範囲外なら、観測と残る問いを`agents_server`の委譲先かAWIへ引き継げ。",
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

async function readGuidanceNotice($: EngineInterface, message: string, sessionId: string | undefined, failures: Map<string, string>): Promise<string | undefined> {
  const lines = reportedLines(message);
  if (!lines.length || sessionId === undefined) return undefined;
  // Python側の共通抽出へ渡すため、点検の項目も通常の報告行へ正規化する。
  const text = lines.map((line) => /^ {0,3}(?:(?:[-*+]|\d+[.)])[ \t]+)?気付いた改善点:/.test(line) ? line : `気付いた改善点: ${line}`).join("\n");
  let diagnostic = "取得状態のprocess.runで例外が発生";
  try {
    const checked = await $.process.run(pluginLaunchCommand($.plugin.root, "agent_toolkit/hook.py", ["improvement_status_advisor"]), {
      stdin: JSON.stringify({ session_id: sessionId, last_assistant_message: text }), timeoutMs: 30_000,
    });
    diagnostic = `終了コード ${checked.exitCode}、出力の切り詰め ${checked.isStdoutTruncated || checked.isStderrTruncated}`;
    if (checked.exitCode !== 0 || checked.isStdoutTruncated || checked.isStderrTruncated) throw new Error(diagnostic);
    diagnostic = "取得案内の出力をJSONとして解析できない";
    const parsed: unknown = JSON.parse(checked.stdout);
    diagnostic = "取得案内の出力が契約外";
    if (parsed === null || typeof parsed !== "object") throw new Error("判定結果がobjectでない");
    const specific = (parsed as { hookSpecificOutput?: { additionalContext?: unknown } }).hookSpecificOutput;
    if (typeof specific?.additionalContext !== "string") throw new Error("取得案内の結果が契約外");
    failures.delete(sessionId);
    return specific.additionalContext || undefined;
  } catch {
    if (failures.get(sessionId) === diagnostic) return undefined;
    failures.set(sessionId, diagnostic);
    return automatedMessage("improvement-read", "warn", `改善点の判定資料の取得状態を確認できなかった。${diagnostic}。` +
      `次の操作: Readのfile_pathに${JSON.stringify(`${$.plugin.root}/share/rules/rules-main.md`)}を指定し、「作業中の改善点の報告」の読込表から判定資料を読め。` +
      "処置とAWI化ステータスを見直し、同じ障害が続く場合はこの診断を委譲元またはユーザーへ報告せよ。");
  }
}

const DISPLAYED = "ユーザーの画面へ表示した。同じ本文の再掲は省き、残工程があれば続行せよ。伝える内容と残工程がない通常終了は、rules-main.claude-code.md「ターンの終え方」に従い省略記号で閉じよ。";
const INVALID_MESSAGE = automatedMessage("send-to-user", "block", "文字列のmessageが無いため、画面へ本文を表示していない。本文をmessageへ入れて呼び直せ。");

export function register(on: On): void {
  // 判定に失敗した場合も、他のツールの呼び出しと本文の表示を止めない。判定不能の診断は会話ごとに1回だけ届ける。
  const delivered = new Map<string, string>();
  const guidanceFailures = new Map<string, string>();
  on("tool.call", async ($, e, next) => {
    if (e.tool !== fullName($.plugin.name)) return next(e);
    const message = messageOf(e);
    if (message === undefined) return { deny: INVALID_MESSAGE };
    let check: LanguageCheck;
    try {
      check = languageCheckResult($.plugin.root, await $.process.run(languageCheckCommand($.plugin.root, message), { timeoutMs: 30_000 }));
    } catch {
      check = languageCheckUnavailable("言語判定のprocess.runで例外が発生");
    }
    let sessionId: string | undefined;
    try {
      sessionId = await $.session.id();
    } catch {
      sessionId = undefined;
    }
    // 英語主体の検出でも判定は成功しているため、判定不能の既往を破棄してから拒否する。
    const note = deliverOnce(delivered, sessionId, check);
    if (check.kind === "violation") return { deny: check.deny };
    const guidance = await readGuidanceNotice($, message, sessionId, guidanceFailures);
    return awiStatusWarnings(message).reduce(withNotice, withNotice(withNotice({ result: DISPLAYED }, note), guidance));
  }).catch(($, e, next) => {
    if (e.tool !== fullName($.plugin.name)) return next(e);
    const message = messageOf(e);
    if (message === undefined) return { deny: INVALID_MESSAGE };
    const note = deliverOnce(delivered, undefined, languageCheckUnavailable(`send_to_userのtool.callで${next.error.kind}が発生`));
    return awiStatusWarnings(message).reduce(withNotice, withNotice({ result: DISPLAYED }, note));
  });

  // 呼び出しの行を`message`の全文の`Markdown`で描く。他のツールの行は描き替えない。
  // 直前の表示と続いて読みにくくならないよう、本文の前に空行1行分の余白を置く。
  on("ui.render", { component: "ToolUse" }, ($, e, next) => {
    if (e.props.tool !== fullName($.plugin.name)) return next(e);
    const message = messageOf(e.props.input);
    const { Markdown, Box } = $.ui.resolve(e);
    // 入力は拒否後もToolUseへ渡る。検査中・拒否・中断の行では本文を描かず、
    // tool.callが言語判定を通して返した成功結果の行だけを表示する。
    if (message === undefined || e.props.isRunning || e.props.isErrored || e.props.isInterrupted || e.props.output !== DISPLAYED) {
      return <Box />;
    }
    return (
      <Box marginTop={1}>
        <Markdown text={message} />
      </Box>
    );
  });

  // 成功結果はモデルへ返し、画面には本文の行だけを残す。エラーは後続の描画で示す。
  on("ui.render", { component: "ToolResult" }, ($, e, next) => {
    if (e.props.tool !== fullName($.plugin.name) || e.props.isErrored) return next(e);
    const { Box } = $.ui.resolve(e);
    return <Box />;
  });
}
