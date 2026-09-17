//! Antigravity CLI statusline: Claude Code statusLineと同じ体裁で状況を示す。
//!
//! Antigravity CLIはstdinへ`agent_state`・`context_window`・`vcs`・`sandbox`・`task_count`・
//! `model`を持つJSONを渡す。Claude Code側と項目が完全には重ならないため、
//! 受け取れる項目だけを同じ区切りと日本語ラベルへ射影する。
//! 1行目はモデル名とcwd、コンテキスト、状態、タスク数、sandboxの有無をパイプ区切りで並べる。
//! 2行目はVCSのブランチを示し、当該項目が無い入力では出力しない。

use serde_json::Value;

use crate::statusline::{
    color, get_nested_number, get_nested_str, home_dir, shorten_home, shorten_worktree_path,
    threshold_color, BLUE, CYAN, GRAY, LABEL_CONTEXT,
};

const LABEL_STATE: &str = "状態";
const LABEL_TASKS: &str = "タスク";
const LABEL_SANDBOX: &str = "sandbox";
const LABEL_BRANCH: &str = "ブランチ";

/// stdinから受け取った生JSON文字列を解釈し、標準出力へ最大2行を出力する。
pub fn run(raw: &str) {
    let Ok(data) = serde_json::from_str::<Value>(raw) else {
        return;
    };
    if !data.is_object() {
        return;
    }
    let home = home_dir();
    let cwd = std::env::current_dir()
        .ok()
        .map(|path| path.to_string_lossy().into_owned());
    for line in render_lines(&data, cwd.as_deref(), home.as_deref()) {
        println!("{line}");
    }
}

fn render_lines(data: &Value, cwd: Option<&str>, home: Option<&str>) -> Vec<String> {
    let mut lines = Vec::new();
    let line1 = render_primary_line(data, cwd, home);
    if !line1.is_empty() {
        lines.push(line1);
    }
    let line2 = render_vcs_line(data);
    if !line2.is_empty() {
        lines.push(line2);
    }
    lines
}

fn render_primary_line(data: &Value, cwd: Option<&str>, home: Option<&str>) -> String {
    let mut segments: Vec<String> = Vec::new();
    let mut head: Vec<String> = Vec::new();
    if let Some(model) = get_nested_str(data, &["model", "display_name"]) {
        head.push(color(&format!("[{model}]"), CYAN));
    }
    if let Some(cwd) = cwd {
        head.push(color(
            &shorten_worktree_path(&shorten_home(cwd, home)),
            BLUE,
        ));
    }
    if !head.is_empty() {
        segments.push(head.join(" "));
    }
    if let Some(pct) = get_nested_number(data, &["context_window", "used_percentage"]) {
        segments.push(color(
            &format!("{LABEL_CONTEXT}: {pct:.0}%"),
            threshold_color(pct),
        ));
    }
    if let Some(state) = get_nested_str(data, &["agent_state"]) {
        segments.push(color(&format!("{LABEL_STATE}: {state}"), GRAY));
    }
    if let Some(tasks) = get_nested_number(data, &["task_count"]) {
        if tasks > 0.0 {
            segments.push(color(&format!("{LABEL_TASKS}: {tasks:.0}"), GRAY));
        }
    }
    if let Some(Value::Bool(enabled)) = data
        .as_object()
        .and_then(|object| object.get("sandbox"))
        .and_then(|sandbox| sandbox.as_object())
        .and_then(|sandbox| sandbox.get("enabled"))
    {
        let text = if *enabled { "on" } else { "off" };
        segments.push(color(&format!("{LABEL_SANDBOX}: {text}"), GRAY));
    }
    segments.join(" | ")
}

fn render_vcs_line(data: &Value) -> String {
    let Some(branch) = get_nested_str(data, &["vcs", "branch"]) else {
        return String::new();
    };
    let dirty = matches!(
        data.as_object()
            .and_then(|object| object.get("vcs"))
            .and_then(|vcs| vcs.as_object())
            .and_then(|vcs| vcs.get("dirty")),
        Some(Value::Bool(true))
    );
    let suffix = if dirty { "*" } else { "" };
    color(&format!("{LABEL_BRANCH}: {branch}{suffix}"), BLUE)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::statusline::RESET;

    #[test]
    fn empty_input_produces_no_lines() {
        assert!(render_lines(&serde_json::json!({}), None, None).is_empty());
    }

    #[test]
    fn model_and_cwd_share_the_claude_code_head_format() {
        let data = serde_json::json!({"model": {"display_name": "gemini-3.8-flash"}});

        let line = render_primary_line(&data, Some("/home/test/work"), Some("/home/test"));

        assert_eq!(
            line,
            format!("{CYAN}[gemini-3.8-flash]{RESET} {BLUE}~/work{RESET}")
        );
    }

    #[test]
    fn context_state_tasks_and_sandbox_join_with_pipes() {
        let data = serde_json::json!({
            "agent_state": "working",
            "context_window": {"used_percentage": 12.0},
            "task_count": 2,
            "sandbox": {"enabled": true},
        });

        let line = render_primary_line(&data, None, None);

        assert!(line.contains("コンテキスト: 12%"));
        assert!(line.contains("状態: working"));
        assert!(line.contains("タスク: 2"));
        assert!(line.contains("sandbox: on"));
        assert_eq!(line.matches(" | ").count(), 3);
    }

    #[test]
    fn dirty_branch_carries_an_asterisk() {
        let data = serde_json::json!({"vcs": {"branch": "develop", "dirty": true}});

        assert_eq!(
            render_vcs_line(&data),
            format!("{BLUE}ブランチ: develop*{RESET}")
        );
    }

    #[test]
    fn absent_vcs_produces_no_second_line() {
        assert_eq!(render_vcs_line(&serde_json::json!({})), "");
    }
}
