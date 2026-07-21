//! Claude Code statusLine: セッション状況を2行で可視化する。
//!
//! `scripts/claude_status_line.py`の後継。stdinから公式statusLine JSON入力を受け取る。
//! 1行目はモデル名・effort・cwd・追加/削除行数・output_style名（既定値以外）を半角スペース区切りで
//! 結合したのち、コンテキスト・コスト・経過時間・消費量(5h/7d)とパイプ区切りで連結する。
//! 2行目はセッションID・セッション名を半角スペース区切りで結合したのち、worktree情報（存在時のみ）
//! とパイプ区切りで連結する。数値項目には日本語ラベルを付与し、欠落・null・空文字列の要素は省略する。

use serde_json::Value;

const RESET: &str = "\x1b[0m";
const RED: &str = "\x1b[31m";
const GREEN: &str = "\x1b[32m";
const YELLOW: &str = "\x1b[33m";
const BLUE: &str = "\x1b[34m";
const MAGENTA: &str = "\x1b[35m";
const CYAN: &str = "\x1b[36m";
const GRAY: &str = "\x1b[90m";

const DEFAULT_OUTPUT_STYLE: &str = "default";
const LABEL_CONTEXT: &str = "コンテキスト";
const LABEL_COST: &str = "コスト";
const LABEL_DURATION: &str = "経過時間";
const LABEL_RATE_LIMITS: &str = "消費量(5h/7d)";
const LABEL_SESSION: &str = "セッション";
const LABEL_WORKTREE: &str = "worktree";

/// stdinから受け取った生JSON文字列を解釈し、標準出力へ最大2行を出力する。
pub fn run(raw: &str) {
    let Ok(data) = serde_json::from_str::<Value>(raw) else {
        return;
    };
    if !data.is_object() {
        return;
    }
    for line in render_lines(&data, home_dir().as_deref()) {
        println!("{line}");
    }
}

/// 入力JSON値を最大2行のステータス文字列へレンダリングする（テスト容易性のため`home`を引数化）。
///
/// `home`はcwd短縮（`~`置換）専用の注入値。本番経路は`home_dir()`を使う。
/// `home_dir()`は`HOME`→`USERPROFILE`の順に参照する簡易実装であり、Windowsの正式な
/// ホーム解決にはレジストリ参照を要する。本ツールはstatusLine表示専用であり、
/// cwd短縮が失敗しても絶対パス表示にフォールバックするだけのためこの簡易実装を許容する。
fn render_lines(data: &Value, home: Option<&str>) -> Vec<String> {
    let mut lines = Vec::new();
    let line1 = render_primary_line(data, home);
    if !line1.is_empty() {
        lines.push(line1);
    }
    let line2 = render_session_line(data);
    if !line2.is_empty() {
        lines.push(line2);
    }
    lines
}

fn render_primary_line(data: &Value, home: Option<&str>) -> String {
    let model_name = get_nested_str(data, &["model", "display_name"]);
    let effort_level = get_nested_str(data, &["effort", "level"]);
    let cwd = get_nested_str(data, &["workspace", "current_dir"]);
    let mut style_name = get_nested_str(data, &["output_style", "name"]);
    if style_name.as_deref() == Some(DEFAULT_OUTPUT_STYLE) {
        style_name = None;
    }
    let ctx_pct = get_nested_number(data, &["context_window", "used_percentage"]);
    let total_cost = get_nested_number(data, &["cost", "total_cost_usd"]);
    let duration_ms = get_nested_number(data, &["cost", "total_duration_ms"]);
    let five_hour_pct = get_nested_number(data, &["rate_limits", "five_hour", "used_percentage"]);
    let seven_day_pct = get_nested_number(data, &["rate_limits", "seven_day", "used_percentage"]);
    let lines_added = get_nested_number(data, &["cost", "total_lines_added"]);
    let lines_removed = get_nested_number(data, &["cost", "total_lines_removed"]);

    let lines_changed_seg = build_lines_changed_segment(lines_added, lines_removed);
    let head = build_head_segment(
        model_name.as_deref(),
        effort_level.as_deref(),
        cwd.as_deref(),
        style_name.as_deref(),
        lines_changed_seg,
        home,
    );

    let mut tail: Vec<String> = Vec::new();
    if let Some(pct) = ctx_pct {
        tail.push(color(
            &format!("{LABEL_CONTEXT}: {pct:.0}%"),
            threshold_color(pct),
        ));
    }
    if let Some(cost) = total_cost {
        tail.push(color(&format!("{LABEL_COST}: ${cost:.2}"), GRAY));
    }
    if let Some(ms) = duration_ms {
        tail.push(color(
            &format!("{LABEL_DURATION}: {}", format_duration(ms)),
            GRAY,
        ));
    }
    if let Some(seg) = build_rate_limits_segment(five_hour_pct, seven_day_pct) {
        tail.push(seg);
    }

    let mut segments: Vec<String> = Vec::new();
    if !head.is_empty() {
        segments.push(head);
    }
    segments.extend(tail);
    segments.join(" | ")
}

/// 2行目: セッションID・セッション名・worktree情報（いずれも存在時のみ）。
fn render_session_line(data: &Value) -> String {
    let session_name = get_nested_str(data, &["session_name"]);
    let session_id = get_nested_str(data, &["session_id"]);
    let worktree_name = get_nested_str(data, &["worktree", "name"]);
    let worktree_branch = get_nested_str(data, &["worktree", "branch"]);

    let mut id_name_parts: Vec<String> = Vec::new();
    if let Some(id) = &session_id {
        id_name_parts.push(color(&format!("{LABEL_SESSION}: {id}"), GRAY));
    }
    if let Some(name) = &session_name {
        id_name_parts.push(color(name, CYAN));
    }

    let mut segments: Vec<String> = Vec::new();
    if !id_name_parts.is_empty() {
        segments.push(id_name_parts.join(" "));
    }
    if let Some(seg) = build_worktree_segment(worktree_name.as_deref(), worktree_branch.as_deref())
    {
        segments.push(seg);
    }
    segments.join(" | ")
}

fn build_worktree_segment(name: Option<&str>, branch: Option<&str>) -> Option<String> {
    let text = match (name, branch) {
        (Some(n), Some(b)) => format!("{LABEL_WORKTREE}: {n} ({b})"),
        (Some(n), None) => format!("{LABEL_WORKTREE}: {n}"),
        (None, Some(b)) => format!("{LABEL_WORKTREE}: ({b})"),
        (None, None) => return None,
    };
    Some(color(&text, BLUE))
}

fn build_lines_changed_segment(added: Option<f64>, removed: Option<f64>) -> Option<String> {
    if added.is_none() && removed.is_none() {
        return None;
    }
    let added = added.unwrap_or(0.0);
    let removed = removed.unwrap_or(0.0);
    Some(format!(
        "{GREEN}+{added:.0}{RESET}/{RED}-{removed:.0}{RESET}"
    ))
}

fn build_head_segment(
    model_name: Option<&str>,
    effort_level: Option<&str>,
    cwd: Option<&str>,
    style_name: Option<&str>,
    lines_changed: Option<String>,
    home: Option<&str>,
) -> String {
    let mut parts: Vec<String> = Vec::new();
    let label = build_model_label(model_name, effort_level);
    if !label.is_empty() {
        parts.push(color(&format!("[{label}]"), CYAN));
    }
    if let Some(cwd) = cwd {
        parts.push(color(&shorten_home(cwd, home), BLUE));
    }
    if let Some(seg) = lines_changed {
        parts.push(seg);
    }
    if let Some(style) = style_name {
        parts.push(color(&format!("@{style}"), MAGENTA));
    }
    parts.join(" ")
}

fn build_model_label(model_name: Option<&str>, effort_level: Option<&str>) -> String {
    match (model_name, effort_level) {
        (Some(m), Some(e)) => format!("{m}|{e}"),
        (Some(m), None) => m.to_string(),
        (None, Some(e)) => e.to_string(),
        (None, None) => String::new(),
    }
}

/// ホームディレクトリ部分を`~`へ短縮する。
fn shorten_home(path: &str, home: Option<&str>) -> String {
    let Some(home) = home else {
        return path.to_string();
    };
    if path == home {
        return "~".to_string();
    }
    for sep in ["/", "\\"] {
        let prefix = format!("{home}{sep}");
        if let Some(rest) = path.strip_prefix(prefix.as_str()) {
            return format!("~{sep}{rest}");
        }
    }
    path.to_string()
}

fn home_dir() -> Option<String> {
    std::env::var("HOME")
        .ok()
        .or_else(|| std::env::var("USERPROFILE").ok())
}

/// ミリ秒を`分秒`または`時間分秒`の日本語形式へ整形する。
fn format_duration(ms: f64) -> String {
    let seconds = (ms / 1000.0) as i64;
    let hours = seconds / 3600;
    let rem = seconds % 3600;
    let minutes = rem / 60;
    let secs = rem % 60;
    if hours > 0 {
        format!("{hours}時間{minutes}分{secs}秒")
    } else {
        format!("{minutes}分{secs}秒")
    }
}

fn build_rate_limits_segment(
    five_hour_pct: Option<f64>,
    seven_day_pct: Option<f64>,
) -> Option<String> {
    match (five_hour_pct, seven_day_pct) {
        (None, None) => None,
        (Some(f), Some(s)) => {
            let display = format!("{f:.0}% / {s:.0}%");
            let c = severer_color(threshold_color(f), threshold_color(s));
            Some(color(&format!("{LABEL_RATE_LIMITS}: {display}"), c))
        }
        (Some(f), None) => Some(color(
            &format!("{LABEL_RATE_LIMITS}: {f:.0}%"),
            threshold_color(f),
        )),
        (None, Some(s)) => Some(color(
            &format!("{LABEL_RATE_LIMITS}: {s:.0}%"),
            threshold_color(s),
        )),
    }
}

/// 2色のうちRED、YELLOW、GREENの順でより警告寄りの色を返す。
fn severer_color(a: &'static str, b: &'static str) -> &'static str {
    for candidate in [RED, YELLOW, GREEN] {
        if a == candidate || b == candidate {
            return candidate;
        }
    }
    a
}

/// 80%超で赤・50%超で黄・それ以下で緑を返す。
fn threshold_color(percentage: f64) -> &'static str {
    if percentage > 80.0 {
        RED
    } else if percentage > 50.0 {
        YELLOW
    } else {
        GREEN
    }
}

fn color(text: &str, code: &str) -> String {
    format!("{code}{text}{RESET}")
}

fn get_nested_str(data: &Value, keys: &[&str]) -> Option<String> {
    match get_nested(data, keys)? {
        Value::String(s) if !s.is_empty() => Some(s.clone()),
        _ => None,
    }
}

fn get_nested_number(data: &Value, keys: &[&str]) -> Option<f64> {
    match get_nested(data, keys)? {
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

fn get_nested<'a>(data: &'a Value, keys: &[&str]) -> Option<&'a Value> {
    let mut current = data;
    for key in keys {
        current = current.as_object()?.get(*key)?;
    }
    Some(current)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn render(data: Value) -> String {
        render_primary_line(&data, Some("/home/test"))
    }

    #[test]
    fn empty_input_produces_no_lines() {
        assert!(render_lines(&serde_json::json!({}), Some("/home/test")).is_empty());
    }

    #[test]
    fn model_and_effort_combine_with_pipe() {
        let data =
            serde_json::json!({"model": {"display_name": "opus"}, "effort": {"level": "xhigh"}});
        assert_eq!(render(data), format!("{CYAN}[opus|xhigh]{RESET}"));
    }

    #[test]
    fn model_only() {
        let data = serde_json::json!({"model": {"display_name": "opus"}});
        assert_eq!(render(data), format!("{CYAN}[opus]{RESET}"));
    }

    #[test]
    fn cwd_exact_home_shortens_to_tilde() {
        let data = serde_json::json!({"workspace": {"current_dir": "/home/test"}});
        assert_eq!(render(data), format!("{BLUE}~{RESET}"));
    }

    #[test]
    fn cwd_under_home_shortens_with_tilde_prefix() {
        let data = serde_json::json!({"workspace": {"current_dir": "/home/test/projects/foo"}});
        assert_eq!(render(data), format!("{BLUE}~/projects/foo{RESET}"));
    }

    #[test]
    fn cwd_outside_home_kept_as_is() {
        let data = serde_json::json!({"workspace": {"current_dir": "/tmp/repo"}});
        assert_eq!(render(data), format!("{BLUE}/tmp/repo{RESET}"));
    }

    #[test]
    fn default_output_style_omitted() {
        let data = serde_json::json!({"output_style": {"name": "default"}});
        assert_eq!(render(data), "");
    }

    #[test]
    fn named_output_style_shown() {
        let data = serde_json::json!({"output_style": {"name": "Explanatory"}});
        assert_eq!(render(data), format!("{MAGENTA}@Explanatory{RESET}"));
    }

    #[test]
    fn context_percentage_color_thresholds() {
        let cases = [
            (0.0, GREEN),
            (50.0, GREEN),
            (51.0, YELLOW),
            (80.0, YELLOW),
            (81.0, RED),
        ];
        for (value, expected) in cases {
            let data = serde_json::json!({"context_window": {"used_percentage": value}});
            assert_eq!(
                render(data),
                format!("{expected}{LABEL_CONTEXT}: {value:.0}%{RESET}")
            );
        }
    }

    #[test]
    fn rate_limits_combined_uses_severer_color() {
        let data = serde_json::json!({"rate_limits": {"five_hour": {"used_percentage": 60}, "seven_day": {"used_percentage": 40}}});
        assert_eq!(
            render(data),
            format!("{YELLOW}{LABEL_RATE_LIMITS}: 60% / 40%{RESET}")
        );
    }

    #[test]
    fn rate_limits_five_hour_only() {
        let data = serde_json::json!({"rate_limits": {"five_hour": {"used_percentage": 81}}});
        assert_eq!(
            render(data),
            format!("{RED}{LABEL_RATE_LIMITS}: 81%{RESET}")
        );
    }

    #[test]
    fn cost_formatted_to_two_decimals() {
        let data = serde_json::json!({"cost": {"total_cost_usd": 0.1234}});
        assert_eq!(render(data), format!("{GRAY}{LABEL_COST}: $0.12{RESET}"));
    }

    #[test]
    fn duration_formats_minutes_and_hours() {
        let cases = [(30 * 1000, "0分30秒"), (3661 * 1000, "1時間1分1秒")];
        for (ms, expected) in cases {
            let data = serde_json::json!({"cost": {"total_duration_ms": ms}});
            assert_eq!(
                render(data),
                format!("{GRAY}{LABEL_DURATION}: {expected}{RESET}")
            );
        }
    }

    #[test]
    fn lines_changed_segment_shown_on_primary_line() {
        let data =
            serde_json::json!({"cost": {"total_lines_added": 156, "total_lines_removed": 23}});
        assert_eq!(render(data), format!("{GREEN}+156{RESET}/{RED}-23{RESET}"));
    }

    #[test]
    fn lines_changed_after_cwd_with_output_style() {
        let data = serde_json::json!({
            "workspace": {"current_dir": "/home/test/repo"},
            "output_style": {"name": "custom"},
            "cost": {"total_lines_added": 11, "total_lines_removed": 11}
        });
        assert_eq!(
            render(data),
            format!(
                "{BLUE}~/repo{RESET} {GREEN}+11{RESET}/{RED}-11{RESET} {MAGENTA}@custom{RESET}"
            )
        );
    }

    #[test]
    fn lines_changed_before_context_in_tail() {
        let data = serde_json::json!({
            "workspace": {"current_dir": "/home/test/repo"},
            "cost": {"total_lines_added": 11, "total_lines_removed": 11},
            "context_window": {"used_percentage": 24.0}
        });
        assert_eq!(
            render(data),
            format!(
                "{BLUE}~/repo{RESET} {GREEN}+11{RESET}/{RED}-11{RESET} | {GREEN}{LABEL_CONTEXT}: 24%{RESET}"
            )
        );
    }

    #[test]
    fn session_line_combines_id_name_worktree() {
        let data = serde_json::json!({
            "session_name": "my-session",
            "session_id": "abc123",
            "worktree": {"name": "my-feature", "branch": "worktree-my-feature"},
        });
        let lines = render_lines(&data, None);
        assert_eq!(lines.len(), 1);
        assert_eq!(
            lines[0],
            format!(
                "{GRAY}{LABEL_SESSION}: abc123{RESET} {CYAN}my-session{RESET} | {BLUE}{LABEL_WORKTREE}: my-feature (worktree-my-feature){RESET}"
            )
        );
    }

    #[test]
    fn session_line_omitted_when_all_fields_absent() {
        let lines = render_lines(
            &serde_json::json!({"model": {"display_name": "opus"}}),
            None,
        );
        assert_eq!(lines.len(), 1);
    }

    #[test]
    fn two_lines_emitted_when_both_present() {
        let data = serde_json::json!({"model": {"display_name": "opus"}, "session_id": "abc123"});
        let lines = render_lines(&data, None);
        assert_eq!(lines.len(), 2);
    }
}
