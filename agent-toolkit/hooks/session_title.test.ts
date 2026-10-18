import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";

// classic入力とskill.promptは公開型定義2.1.277に従う。session_titleは本体2.1.302の入力欄。
const INPUT = { session_id: "title-test", transcript_path: "/transcript", cwd: "/work", permission_mode: "default", hook_event_name: "UserPromptSubmit" as const, prompt: "作業を続ける" };

function stateStore(on: On, state: Record<string, string> = {}): Record<string, string> {
  on("session.id", () => ({ value: "title-test" }));
  on("process.run", (_$, e) => {
    const kind = e.argv.find((arg) => arg.startsWith("--kind="));
    const title = e.argv.find((arg) => arg.startsWith("--title="));
    if (kind) state.session_title_kind = kind.slice(7);
    if (title) state.last_hook_session_title = title.slice(8);
    return { value: { exitCode: 0, stdout: JSON.stringify(state), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
  });
  on("skill.prompt", (_$, e) => ({ text: e.text }));
  on("classic.UserPromptSubmit", () => ({ additionalContext: ["既存の注記"] }));
  return state;
}

for (const [skill, prefix] of [["process-wi", "process-wi"], ["single-lane-process", "single-lane"], ["add-awi-by-user", "add-awi"]]) {
  for (const plan of [false, true]) {
    test(`${skill}の短い名前を設定する（計画=${plan}）`, async ($, on) => {
      mock.env(on, {});
      stateStore(on, plan ? { current_plan_file_path: "/plans/18-1921_画像整理.md" } : {});
      await $.skill.prompt({ skill: `agent-toolkit:${skill}`, text: "スキル本文" });
      const result = await $.classic.UserPromptSubmit(INPUT);
      expect(result.sessionTitle).toBe(plan ? `${prefix} 画像整理` : prefix);
      expect(result.additionalContext).toEqual(["既存の注記"]);
    });
  }
}

test("その他は計画名だけを設定し、計画が無ければホストへ任せる", async ($, on) => {
  mock.env(on, {});
  const state = stateStore(on);
  expect((await $.classic.UserPromptSubmit(INPUT)).sessionTitle).toBeUndefined();
  state.current_plan_file_path = "C:\\plans\\18-1921_画像整理.md";
  expect((await $.classic.UserPromptSubmit(INPUT)).sessionTitle).toBe("画像整理");
});

test("手動名を保持し、前回設定名から種別と計画の変更を反映する", async ($, on) => {
  mock.env(on, {});
  const state = stateStore(on, { current_plan_file_path: "/plans/18-1921_画像整理.md" });
  expect((await $.classic.UserPromptSubmit({ ...INPUT, session_title: "手動の名前" })).sessionTitle).toBeUndefined();
  expect(state.last_hook_session_title).toBeUndefined();
  const first = (await $.classic.UserPromptSubmit(INPUT)).sessionTitle;
  await $.skill.prompt({ skill: "process-wi", text: "スキル本文" });
  expect((await $.classic.UserPromptSubmit({ ...INPUT, session_title: first })).sessionTitle).toBe("process-wi 画像整理");
  state.current_plan_file_path = "/plans/18-2000_次の計画.md";
  expect((await $.classic.UserPromptSubmit({ ...INPUT, session_title: "process-wi 画像整理" })).sessionTitle).toBe("process-wi 次の計画");
  expect((await $.classic.UserPromptSubmit({ ...INPUT, session_title: "process-wi 次の計画" })).sessionTitle).toBeUndefined();
});

for (const expectedId of [undefined, "title-test", "parent", ""]) {
  test(`process-loopの会話IDを照合する（${String(expectedId)}）`, async ($, on) => {
    mock.env(on, { AGENT_TOOLKIT_PROCESS_LOOP_SESSION: "1", ...(expectedId === undefined ? {} : { AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID: expectedId }) });
    stateStore(on);
    expect((await $.classic.UserPromptSubmit(INPUT)).sessionTitle).toBe(expectedId === undefined || expectedId === "title-test" ? "process-wi" : undefined);
  });
}

test("独立した委譲先も同じ命名経路を使う", async ($, on) => {
  mock.env(on, { AGENT_TOOLKIT_DELEGATED_SESSION: "1" });
  stateStore(on);
  await $.skill.prompt({ skill: "single-lane-process", text: "スキル本文" });
  expect((await $.classic.UserPromptSubmit(INPUT)).sessionTitle).toBe("single-lane");
});
