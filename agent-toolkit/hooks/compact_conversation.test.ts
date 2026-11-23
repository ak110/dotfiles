import type { On } from "claude-code";
import { type Engine, expect, mock, test } from "claude-code/testing";
import { register } from "./register.ts";

function registrationViolations(event: unknown, matcher: unknown): string[] {
  const matches = (matcher: unknown, value: string): boolean => {
    if (matcher === undefined) return true;
    if (typeof matcher === "string") return matcher === value;
    if (matcher instanceof RegExp) { matcher.lastIndex = 0; return matcher.test(value); }
    throw new Error(`未対応のmatcher: ${String(matcher)}`);
  };
  const protectedEvents = ["prompt.section", "prompt.context", "prompt.attachment", "prompt.compose", "skill.prompt", "attribution.text", "settings.read"];
  const violations = protectedEvents.filter(name => matches(event, name));
  if (typeof event === "string" && event.startsWith("classic.")) violations.push(event);
  if (matches(event, "session.append")) {
    const door = typeof matcher === "object" && matcher !== null ? (matcher as { door?: unknown }).door : undefined;
    violations.push(...["attachment", "hook-context", "compaction"].filter(name => matches(door, name)));
  }
  return violations;
}

test("圧縮置換以外の実登録matcherは組織ガードの保護対象を購読しない", () => {
  let count = 0;
  const capture = ((event: unknown, matcher: unknown) => {
    count++;
    expect(registrationViolations(event, matcher)).toEqual([]);
    return { catch: () => undefined };
  }) as unknown as On;
  register(capture, {});
  expect(count > 0).toBe(true);
});

for (const trigger of ["manual", "auto", "plugin", "precompute"] as const) {
  test(`main会話の${trigger}は原本を参照して要約を呼ばない`, async ($, on) => {
    mock.env(on, {});
    let summaries = 0;
    on("session.id", () => ({ value: "current-session" }));
    on("process.run", (_$, e) => {
      expect(e.argv.slice(-2)).toEqual(["--session-id", "current-session"]);
      return { value: { exitCode: 0, stdout: JSON.stringify({ record_path: "/records/current-session.jsonl" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
    });
    on("session.compact", () => { summaries++; return { messages: [] }; });
    const result = await $.session.compact({ trigger, messages: [] });
    expect(summaries).toBe(0);
    if (trigger === "precompute") {
      expect(result.skip).toMatch("先行生成");
    } else {
      expect(result.messages?.length).toBe(1);
      const text = result.messages?.[0]?.text ?? "";
      expect(text).toMatch(/^<[^>]+>\s*会話が圧縮され、圧縮前のやり取りと要約は現在の文脈にない。/);
      expect(text).toContain("直後の復元通知が示す作業契約と会話の要約を読み、作業状態を復元せよ");
      expect(text).not.toMatch(/会話の要約生成を省き|会話ID:|原本ログ:|\/records\/current-session.jsonl/);
      expect("usage" in result).toBe(false);
    }
  });
}

for (const condition of ["subagent", "unresolved", "failure", "truncated"] as const) {
  test(`原本置換できない${condition}では標準圧縮を保つ`, async ($, on) => {
    mock.env(on, {});
    let summaries = 0;
    let resolutions = 0;
    on("session.id", () => ({ value: "current-session" }));
    on("process.run", () => {
      resolutions++;
      if (condition === "failure") throw new Error("記録を取得できない");
      return { value: { exitCode: condition === "unresolved" ? 2 : 0, stdout: JSON.stringify({ record_path: "/records/current-session.jsonl" }), stderr: "", isStdoutTruncated: condition === "truncated", isStderrTruncated: false } };
    });
    on("session.compact", () => { summaries++; return { messages: [{ role: "user", text: "標準の圧縮結果", toolUses: [] }] }; });
    const result = await $.session.compact({ trigger: "manual", messages: [], ...(condition === "subagent" ? { agentId: "child" } : {}) });
    expect(summaries).toBe(1);
    expect(resolutions).toBe(condition === "subagent" ? 0 : 1);
    expect(result.messages?.[0]?.text).toBe("標準の圧縮結果");
  });
}

test("他機能への保護door・無条件・広域イベント登録の持込みを検出する", () => {
  for (const matcher of [{ door: "attachment" }, { door: /^(delivery|queue|attachment)$/ }, { door: /hook-context/ }, { door: /compaction/g }, {}]) {
    expect(registrationViolations("session.append", matcher).length > 0).toBe(true);
  }
  expect(registrationViolations("session.append", () => undefined).length > 0).toBe(true);
  expect(registrationViolations(/.*/, {}).length > 0).toBe(true);
  expect(registrationViolations("session.append", { door: /^(delivery|queue)$/ })).toEqual([]);
});


for (const scenario of ["unrequested", "default", "resume", "skip", "failure", "headless", "lost-surface"] as const) {
  test(`圧縮への明示指定と実行結果で自動再開を決める: ${scenario}`, async ($, on) => {
    mock.env(on, { HOME: "/home/tester" });
    const clock = mock.clock(on);
    const events: string[] = [];
    const id = "f".repeat(32);
    const source = `ui-request-compact-resume-${id}.txt`;
    on("session.id", () => ({ value: "compact-resume" }));
    on("session.start", (_$, e) => ({ cwd: e.cwd }));
    on("command.register", (_$, e) => ({ value: { command: e.name } }));
    on("session.messages", () => ({ value: [] }));
    on("turn.start", (_$, e) => ({ turnId: e.turnId }));
    on("turn.complete", () => ({ text: "" }));
    on("fs.list", () => ({ value: scenario === "unrequested" ? [] : [{ name: source, kind: "file", size: 1, mtimeMs: 0, isLink: false }] }));
    on("fs.read", (_$, e) => ({ value: e.path.endsWith(source) ? JSON.stringify({ id, operation: "compact_conversation", input: { resume: scenario !== "default" }, expires: 99999999999 }) : "consumed" }));
    on("fs.write", () => ({ value: undefined }));
    on("fs.exists", () => ({ value: true }));
    on("process.run", () => ({ value: { exitCode: 2, stdout: "", stderr: "", isStdoutTruncated: false, isStderrTruncated: false } }));
    let hasSurface = scenario !== "headless";
    on("session.surfaces", () => ({ value: hasSurface ? ["terminal"] : [] }));
    on("session.usage", () => ({ value: { startedAt: 0, context: { tokens: 1000, window: 1_000_000, percent: 1 }, rateLimits: [] } }));
    on("session.compact", () => {
      events.push("compact");
      if (scenario === "failure") throw new Error("圧縮に失敗");
      return scenario === "skip" ? { skip: "見送り" } : { messages: [{ role: "user", text: "圧縮結果", toolUses: [] }] };
    });
    on("command.run", () => { events.push("command"); return {}; });
    on("prompt.submit", () => { events.push("resume"); return { text: "" }; });
    on("ui.toast", () => ({ value: undefined }));
    await $.session.start({ cwd: "/work", surface: null, isInteractive: scenario !== "headless" });
    await $.turn.start({ text: "圧縮を予約する", turnId: "compact-turn" });
    await clock.advance(200);
    if (scenario === "lost-surface") hasSurface = false;
    expect(events).toEqual([]);
    const finish = { answer: "", durationMs: 0, isAborted: false, turnId: "compact-turn", reason: "answer" as const };
    await $.turn.complete(finish);
    await clock.advance(1);
    await $.turn.complete(finish);
    await clock.advance(1);
    expect(events).toEqual(scenario === "resume" ? ["compact", "resume"] : scenario === "unrequested" || scenario === "headless" || scenario === "lost-surface" ? [] : ["compact"]);
  });
}
