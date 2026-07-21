//! Claude Code subagentStatusLine: サブエージェント行に名前・モデル短縮名・使用率等を表示する。
//!
//! `scripts/claude_subagent_status_line.py`の後継。stdinから公式subagentStatusLine JSON入力
//! （`columns`・`tasks`配列）を受け取る。タスクごとに名前列`{名前} ({短縮モデル名})`と
//! `description`を左側に、`経過時間 · トークン数(k単位)/使用率% · status`の右寄せグループを
//! 右側に配置した1行を`{"id": <task id>, "content": <行>}`のJSON行として標準出力へ出力する。
//!
//! 名前列は同一入力内の全タスクを走査して表示幅を揃え（`columns // 3`セル上限）、
//! 上限超過時はモデル名を保持したまま名前部分のみ省略記号付きで切り詰める
//! （モデル名だけで上限を超える極端な場合は名前列全体を切り詰める）。
//! 名前の由来は`name`→`label`→`type`の順で非空文字列を採用する
//! （実ペイロードは`name`フィールドを持たず`label`に`description`と実質同一の値が入る。
//! Claude Code 2.1.214時点で実測確認済み）。`label`値が`description`（正規化後）と一致する
//! 場合は`label`を採用せず`type`を採用する（`description`欄と重複表示になる冗長を避けるため）。
//! `model`未提供タスクは括弧書き省略、名前・モデル双方欠落時は名前列が空文字列になる。
//! 使用率はtokenCount/contextWindowSizeから算出する（いずれか欠落・非数値・
//! contextWindowSizeが0以下の場合は省略）。経過時間は`startTime`
//! （エポックミリ秒またはISO 8601）から算出する。`description`は改行を空白へ置換して
//! 1行化し、連続空白を1個へ畳んでから表示幅（East Asian WidthのW/F/A、曖昧幅を含め
//! 全角2セル換算）で残り幅へ切り詰める。`id`欠落タスクは出力対象外とする。
//! 最終行は端末幅`columns`セル以内へ収める。
//!
//! `name`指定＋`run_in_background=true`起動のnamed subagent（teammate）はタスク種別
//! `in_process_teammate`として管理され、本モジュールの適用対象`local_agent`型から
//! 構造的に除外される（Claude Code 2.1.214実測確認。ドキュメント未記載の制約であり
//! 本ツール側では対処しない）。

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value};
use unicode_width::UnicodeWidthChar;

const SEP: &str = " · ";
const ELLIPSIS: &str = "…";
const GAP_MIN: usize = 2;
const DEFAULT_COLUMNS: usize = 80;
const NAME_WIDTH_DIVISOR: usize = 3;
const MODEL_SHORT_PATTERNS: &[(&str, &str)] = &[
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
    ("fable", "Fable"),
];

/// stdinから受け取った生JSON文字列を解釈し、タスクごとのJSON行を標準出力へ出力する。
pub fn run(raw: &str) {
    let Ok(data) = serde_json::from_str::<Value>(raw) else {
        return;
    };
    for (id, content) in render_all(&data) {
        println!("{}", serde_json::json!({"id": id, "content": content}));
    }
}

fn render_all(data: &Value) -> Vec<(String, String)> {
    let Some(obj) = data.as_object() else {
        return Vec::new();
    };
    let width = obj
        .get("columns")
        .and_then(|v| v.as_i64())
        .filter(|&c| c > 0)
        .map(|c| c as usize)
        .unwrap_or(DEFAULT_COLUMNS);
    let Some(tasks) = obj.get("tasks").and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    let now = Utc::now();
    let name_width = compute_name_width(tasks, width);
    let mut out = Vec::new();
    for task in tasks {
        let Some(task_obj) = task.as_object() else {
            continue;
        };
        let Some(id) = task_obj
            .get("id")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
        else {
            continue;
        };
        let Some(content) = render_task(task_obj, width, now, Some(name_width)) else {
            continue;
        };
        out.push((id.to_string(), content));
    }
    out
}

fn compute_name_width(tasks: &[Value], width: usize) -> usize {
    let cap = width / NAME_WIDTH_DIVISOR;
    tasks
        .iter()
        .filter_map(|t| t.as_object())
        .filter(|t| matches!(t.get("id"), Some(Value::String(s)) if !s.is_empty()))
        .map(|t| {
            let description = task_description(t);
            display_width(&name_column(t, &description)).min(cap)
        })
        .max()
        .unwrap_or(0)
}

fn task_description(task: &Map<String, Value>) -> String {
    match task.get("description") {
        Some(Value::String(s)) => normalize_description(s),
        _ => String::new(),
    }
}

/// タスク1件を1行分の`content`文字列へレンダリングする。`id`欠落・空文字時はNoneを返す。
///
/// `name_width`省略時は当該タスク単独の名前列幅（`columns // 3`セル上限）を用いる。
pub fn render_task(
    task: &Map<String, Value>,
    width: usize,
    now: DateTime<Utc>,
    name_width: Option<usize>,
) -> Option<String> {
    let has_id = matches!(task.get("id"), Some(Value::String(s)) if !s.is_empty());
    if !has_id {
        return None;
    }

    let description = task_description(task);

    let cap = width / NAME_WIDTH_DIVISOR;
    let col_width =
        name_width.unwrap_or_else(|| display_width(&name_column(task, &description)).min(cap));
    let name_present = col_width > 0;
    let mut padded_name = String::new();
    if name_present {
        let name_col = fit_name_column(task, col_width, &description);
        let pad = col_width.saturating_sub(display_width(&name_col));
        padded_name = format!("{name_col}{}", " ".repeat(pad));
    }

    let right_parts = build_right_parts(task, now);
    let right = right_parts.join(SEP);
    let right_present = !right.is_empty();

    let mut reserved = if name_present {
        display_width(&padded_name)
    } else {
        0
    };
    if name_present && !description.is_empty() {
        reserved += GAP_MIN;
    }
    if right_present {
        reserved += GAP_MIN + display_width(&right);
    }
    let desc_budget = width.saturating_sub(reserved);
    let desc = if !description.is_empty() {
        truncate(&description, desc_budget)
    } else {
        String::new()
    };

    let mut left_parts: Vec<&str> = Vec::new();
    if name_present {
        left_parts.push(&padded_name);
    }
    if !desc.is_empty() {
        left_parts.push(&desc);
    }
    let left = left_parts.join(&" ".repeat(GAP_MIN));

    let line = if right_present {
        let width_i = width as i64;
        let base_gap = width_i - display_width(&left) as i64 - display_width(&right) as i64;
        let min_gap = if !left.is_empty() { GAP_MIN as i64 } else { 0 };
        let gap = base_gap.max(min_gap) as usize;
        format!("{left}{}{right}", " ".repeat(gap))
    } else {
        left
    };

    let line = line.trim_end().to_string();
    Some(if display_width(&line) > width {
        truncate(&line, width)
    } else {
        line
    })
}

fn name_column(task: &Map<String, Value>, description: &str) -> String {
    let name = name_label(task, description);
    let model_label = model_label_of(task);
    match (name.is_empty(), model_label) {
        (false, Some(m)) => format!("{name} ({m})"),
        (false, None) => name,
        (true, Some(m)) => format!("({m})"),
        (true, None) => String::new(),
    }
}

/// 名前列を`width_cap`セル以内で組み立てる。モデル名を保持したまま名前部分のみ切り詰める。
///
/// モデル名だけで`width_cap`を超える極端な場合は名前列全体を省略記号付きで切り詰める。
fn fit_name_column(task: &Map<String, Value>, width_cap: usize, description: &str) -> String {
    if width_cap == 0 {
        return String::new();
    }
    let name = name_label(task, description);
    let model_label = model_label_of(task);
    match (name.is_empty(), model_label) {
        (false, Some(m)) => {
            let suffix = format!(" ({m})");
            let full = format!("{name}{suffix}");
            if display_width(&full) <= width_cap {
                return full;
            }
            let suffix_w = display_width(&suffix);
            if suffix_w >= width_cap {
                truncate(&full, width_cap)
            } else {
                format!("{}{suffix}", truncate(&name, width_cap - suffix_w))
            }
        }
        (false, None) => truncate(&name, width_cap),
        (true, Some(m)) => truncate(&format!("({m})"), width_cap),
        (true, None) => String::new(),
    }
}

fn model_label_of(task: &Map<String, Value>) -> Option<String> {
    task.get("model")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(short_model_name)
}

/// 名前の由来を`name`→`label`→`type`の順で非空文字列を採用する。全欠落時は空文字列。
///
/// 採用値は`normalize_description`で改行・連続空白を1行化してから返す
/// （名前列は1エントリ1行の前提であり、`description`同様に改行混入を防ぐため）。
/// `label`値を正規化した結果が非空の`description`と一致する場合は`label`を採用せず`type`へ
/// フォールバックする（`label`に`description`と同一内容が入る既知の重複パターンのため。
/// フォールバック先も`description`と一致する場合はそのまま採用する）。
fn name_label(task: &Map<String, Value>, description: &str) -> String {
    for key in ["name", "label", "type"] {
        let Some(raw) = task
            .get(key)
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
        else {
            continue;
        };
        let normalized = normalize_description(raw);
        if key == "label" && !description.is_empty() && normalized == description {
            continue;
        }
        return normalized;
    }
    String::new()
}

fn build_right_parts(task: &Map<String, Value>, now: DateTime<Utc>) -> Vec<String> {
    let mut parts = Vec::new();
    if let Some(elapsed) = format_elapsed(task.get("startTime"), now) {
        parts.push(elapsed);
    }
    if let Some(tokens) = format_tokens(task.get("tokenCount"), task.get("contextWindowSize")) {
        parts.push(tokens);
    }
    if let Some(status) = task
        .get("status")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        parts.push(status.to_string());
    }
    parts
}

/// トークン数と使用率%を`176.2k/18%`形式へ整形する。使用率算出不能時はトークン数のみ。
fn format_tokens(
    token_count: Option<&Value>,
    context_window_size: Option<&Value>,
) -> Option<String> {
    let count = as_number(token_count)?;
    let mut text = format!("{:.1}k", count / 1000.0);
    if let Some(pct) = context_usage_pct(token_count, context_window_size) {
        text.push_str(&format!("/{pct:.0}%"));
    }
    Some(text)
}

fn context_usage_pct(
    token_count: Option<&Value>,
    context_window_size: Option<&Value>,
) -> Option<f64> {
    let count = as_number(token_count)?;
    let window = as_number(context_window_size)?;
    if window <= 0.0 {
        return None;
    }
    Some(count / window * 100.0)
}

fn as_number(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(n)) => n.as_f64(),
        _ => None,
    }
}

/// `startTime`からの経過時間を`1h23m`・`4m56s`・`45s`形式へ整形する。解釈不能・未来時刻はNone。
fn format_elapsed(start_time: Option<&Value>, now: DateTime<Utc>) -> Option<String> {
    let start = parse_start_time(start_time?)?;
    let seconds = (now - start).num_seconds();
    if seconds < 0 {
        return None;
    }
    if seconds >= 3600 {
        Some(format!("{}h{}m", seconds / 3600, seconds % 3600 / 60))
    } else if seconds >= 60 {
        Some(format!("{}m{}s", seconds / 60, seconds % 60))
    } else {
        Some(format!("{seconds}s"))
    }
}

/// エポックミリ秒数値またはISO 8601文字列をaware datetimeへ変換する。解釈不能はNone。
fn parse_start_time(value: &Value) -> Option<DateTime<Utc>> {
    match value {
        Value::Number(n) => {
            let ms = n.as_f64()?;
            let secs = (ms / 1000.0).floor() as i64;
            let nanos = ((ms / 1000.0 - secs as f64) * 1e9).round() as u32;
            DateTime::from_timestamp(secs, nanos)
        }
        Value::String(s) => {
            let normalized = s.replace('Z', "+00:00");
            if let Ok(dt) = DateTime::parse_from_rfc3339(&normalized) {
                return Some(dt.with_timezone(&Utc));
            }
            // オフセット省略のnaive ISO 8601も許容する（Python fromisoformatの既定UTC解釈に合わせる）。
            NaiveDateTime::parse_from_str(&normalized, "%Y-%m-%dT%H:%M:%S%.f")
                .ok()
                .map(|naive| naive.and_utc())
        }
        _ => None,
    }
}

/// `description`内の改行を空白へ置換し、連続する空白を1個へ畳んで1行化する。
fn normalize_description(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn short_model_name(model_id: &str) -> String {
    let lowered = model_id.to_lowercase();
    for (pattern, label) in MODEL_SHORT_PATTERNS {
        if lowered.contains(pattern) {
            return (*label).to_string();
        }
    }
    model_id.to_string()
}

/// East Asian Width基準の表示幅を返す。`W`・`F`・`A`（曖昧幅を含む）の文字は2セル、他は1セル換算。
///
/// `unicode-width`クレートの`width_cjk()`はAmbiguous幅を2カラム扱いする
/// （`width()`は1カラム扱いのため不採用）。
fn display_width(text: &str) -> usize {
    text.chars().map(|c| c.width_cjk().unwrap_or(1)).sum()
}

/// 文字列を表示幅`budget`セル以内へ省略記号付きで切り詰める。
///
/// 省略記号`…`（U+2026）はEast Asian Widthが`A`（曖昧幅）のため`display_width`基準で
/// 2セルを占める。ハードコードした1セル前提は表示幅超過を招くため、実測幅を予約する。
fn truncate(text: &str, budget: usize) -> String {
    if budget == 0 {
        return String::new();
    }
    if display_width(text) <= budget {
        return text.to_string();
    }
    let ellipsis_width = display_width(ELLIPSIS);
    if budget < ellipsis_width {
        return String::new();
    }
    let mut result = String::new();
    let mut used = 0usize;
    for ch in text.chars() {
        let w = ch.width_cjk().unwrap_or(1);
        if used + w > budget - ellipsis_width {
            break;
        }
        result.push(ch);
        used += w;
    }
    result.push_str(ELLIPSIS);
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW_SECS: i64 = 1_767_225_600; // 2026-01-01T00:00:00Z

    fn now() -> DateTime<Utc> {
        DateTime::from_timestamp(NOW_SECS, 0).unwrap()
    }

    fn task(pairs: &[(&str, Value)]) -> Map<String, Value> {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), v.clone()))
            .collect()
    }

    fn render(t: &Map<String, Value>) -> Option<String> {
        render_task(t, 80, now(), None)
    }

    #[test]
    fn missing_id_returns_none() {
        let t = task(&[("name", "foo".into())]);
        assert_eq!(render(&t), None);
    }

    #[test]
    fn empty_id_returns_none() {
        let t = task(&[("id", "".into()), ("name", "foo".into())]);
        assert_eq!(render(&t), None);
    }

    #[test]
    fn name_only() {
        let t = task(&[("id", "t1".into()), ("name", "foo".into())]);
        assert_eq!(render(&t), Some("foo".to_string()));
    }

    #[test]
    fn name_fallback_order_name_then_label_then_type() {
        let cases: Vec<(Map<String, Value>, &str)> = vec![
            (task(&[("id", "t1".into()), ("label", "foo".into())]), "foo"),
            (
                task(&[("id", "t1".into()), ("type", "local_agent".into())]),
                "local_agent",
            ),
            (
                task(&[
                    ("id", "t1".into()),
                    ("name", "foo".into()),
                    ("label", "bar".into()),
                ]),
                "foo",
            ),
            (
                task(&[
                    ("id", "t1".into()),
                    ("label", "bar".into()),
                    ("type", "local_agent".into()),
                ]),
                "bar",
            ),
        ];
        for (t, expected) in cases {
            assert_eq!(render(&t), Some(expected.to_string()));
        }
    }

    #[test]
    fn label_matching_description_falls_back_to_type() {
        let t = task(&[
            ("id", "t1".into()),
            ("label", "同一文言".into()),
            ("type", "local_agent".into()),
            ("description", "同一文言".into()),
        ]);
        assert_eq!(render(&t), Some("local_agent  同一文言".to_string()));
    }

    #[test]
    fn label_matching_description_without_type_omits_name() {
        let t = task(&[
            ("id", "t1".into()),
            ("label", "同一文言".into()),
            ("description", "同一文言".into()),
        ]);
        assert_eq!(render(&t), Some("同一文言".to_string()));
    }

    #[test]
    fn label_matching_description_with_newline_falls_back_to_type() {
        let t = task(&[
            ("id", "t1".into()),
            ("label", "計画B（trim）起草を委譲\n".into()),
            ("type", "local_agent".into()),
            ("description", "計画B（trim）起草を委譲".into()),
        ]);
        assert_eq!(
            render(&t),
            Some("local_agent  計画B（trim）起草を委譲".to_string())
        );
    }

    #[test]
    fn model_only_without_name() {
        let t = task(&[("id", "t1".into()), ("model", "claude-sonnet-5".into())]);
        assert_eq!(render(&t), Some("(Sonnet)".to_string()));
    }

    #[test]
    fn name_and_model_both_missing_renders_empty() {
        let t = task(&[("id", "t1".into())]);
        assert_eq!(render(&t), Some(String::new()));
    }

    #[test]
    fn model_short_names() {
        let cases = [
            ("claude-opus-4-8", "Opus"),
            ("claude-sonnet-5", "Sonnet"),
            ("claude-haiku-4-5", "Haiku"),
            ("claude-fable-1", "Fable"),
            ("unknown-model-x", "unknown-model-x"),
        ];
        for (model_id, expected) in cases {
            let t = task(&[
                ("id", "t1".into()),
                ("name", "foo".into()),
                ("model", model_id.into()),
            ]);
            assert_eq!(render(&t), Some(format!("foo ({expected})")));
        }
    }

    #[test]
    fn description_newlines_collapsed_to_spaces() {
        let t = task(&[
            ("id", "t1".into()),
            ("name", "foo".into()),
            ("description", "l1\nl2\r\nl3\rl4   l5".into()),
        ]);
        assert_eq!(
            render_task(&t, 200, now(), None),
            Some("foo  l1 l2 l3 l4 l5".to_string())
        );
    }

    #[test]
    fn description_truncated_to_fit_width() {
        let t = task(&[
            ("id", "t1".into()),
            ("name", "foo".into()),
            ("description", "x".repeat(100).into()),
        ]);
        let result = render_task(&t, 10, now(), None).unwrap();
        assert!(display_width(&result) <= 10);
        assert!(result.ends_with('…'));
    }

    #[test]
    fn name_truncated_but_model_preserved() {
        let t = task(&[
            ("id", "t1".into()),
            ("name", "x".repeat(20).into()),
            ("model", "claude-sonnet-5".into()),
        ]);
        assert_eq!(
            render_task(&t, 80, now(), Some(13)),
            Some("xx… (Sonnet)".to_string())
        );
    }

    #[test]
    fn fullwidth_name_truncated_with_ellipsis() {
        let t = task(&[("id", "t1".into()), ("name", "あいうえお".into())]);
        assert_eq!(render_task(&t, 80, now(), Some(4)), Some("あ…".to_string()));
    }

    #[test]
    fn tokens_in_k_unit_right_aligned() {
        let t = task(&[
            ("id", "t1".into()),
            ("name", "foo".into()),
            ("tokenCount", 176183.into()),
        ]);
        let result = render(&t).unwrap();
        assert!(result.starts_with("foo "));
        assert!(result.ends_with("176.2k"));
        assert_eq!(display_width(&result), 80);
    }

    #[test]
    fn usage_percentage_joined_with_slash() {
        let t = task(&[
            ("id", "t1".into()),
            ("name", "foo".into()),
            ("tokenCount", 1500.into()),
            ("contextWindowSize", 200000.into()),
        ]);
        let result = render(&t).unwrap();
        assert!(result.ends_with("1.5k/1%"));
    }

    #[test]
    fn usage_percentage_omitted_on_invalid_context_window() {
        for cw in [
            Value::from(0),
            Value::from(-1),
            Value::Null,
            Value::from("invalid"),
        ] {
            let t = task(&[
                ("id", "t1".into()),
                ("name", "foo".into()),
                ("tokenCount", 1500.into()),
                ("contextWindowSize", cw),
            ]);
            let result = render(&t).unwrap();
            assert!(result.ends_with("1.5k"));
        }
    }

    #[test]
    fn elapsed_formats() {
        let cases: [(Value, &str); 2] = [
            (Value::from((NOW_SECS - 45) * 1000), "45s"),
            (Value::from("2025-12-31T23:55:04+00:00"), "4m56s"),
        ];
        for (start_time, expected) in cases {
            let t = task(&[
                ("id", "t1".into()),
                ("name", "foo".into()),
                ("startTime", start_time),
            ]);
            let result = render(&t).unwrap();
            assert!(result.ends_with(expected));
        }
    }

    #[test]
    fn elapsed_omitted_on_invalid_or_future_start() {
        for start_time in [
            Value::Bool(true),
            Value::from("not-a-date"),
            Value::Null,
            Value::from((NOW_SECS + 60) * 1000),
        ] {
            let t = task(&[
                ("id", "t1".into()),
                ("name", "foo".into()),
                ("startTime", start_time),
            ]);
            assert_eq!(render(&t), Some("foo".to_string()));
        }
    }

    #[test]
    fn full_combination() {
        let t = task(&[
            ("id", "t1".into()),
            ("name", "impl".into()),
            ("description", "実装作業中".into()),
            ("model", "claude-opus-4-8".into()),
            ("tokenCount", 1500.into()),
            ("contextWindowSize", 200000.into()),
            ("status", "running".into()),
            ("startTime", ((NOW_SECS - 45) * 1000).into()),
        ]);
        let result = render(&t).unwrap();
        assert!(result.starts_with("impl (Opus)  実装作業中"));
        assert!(result.ends_with(&format!("45s{SEP}1.5k/1%{SEP}running")));
        assert_eq!(display_width(&result), 80);
    }

    #[test]
    fn compute_name_width_aligns_description_start_across_tasks() {
        let tasks = vec![
            Value::Object(task(&[
                ("id", "t1".into()),
                ("name", "ab".into()),
                ("description", "d1".into()),
            ])),
            Value::Object(task(&[
                ("id", "t2".into()),
                ("name", "reviewer-x".into()),
                ("description", "d2".into()),
            ])),
        ];
        let width = compute_name_width(&tasks, 80);
        let t1 = tasks[0].as_object().unwrap();
        let t2 = tasks[1].as_object().unwrap();
        let c1 = render_task(t1, 80, now(), Some(width)).unwrap();
        let c2 = render_task(t2, 80, now(), Some(width)).unwrap();
        assert_eq!(c1.find("d1"), c2.find("d2"));
    }

    #[test]
    fn zero_width_name_column_omits_leading_gap() {
        let t = task(&[("id", "t1".into()), ("description", "bar".into())]);
        let result = render_task(&t, 2, now(), None).unwrap();
        assert!(!result.starts_with(' '));
    }

    #[test]
    fn invalid_or_empty_input_produces_no_output() {
        for raw in ["", "not json", "[]", "{}"] {
            let data: Value = serde_json::from_str(raw).unwrap_or(Value::Null);
            assert!(render_all(&data).is_empty());
        }
    }

    #[test]
    fn columns_bounds_output_width() {
        let data = serde_json::json!({"columns": 10, "tasks": [{"id": "t1", "name": "foo", "description": "x".repeat(100)}]});
        let out = render_all(&data);
        assert_eq!(out.len(), 1);
        assert!(display_width(&out[0].1) <= 10);
    }

    #[test]
    fn tasks_without_valid_id_are_skipped() {
        let data = serde_json::json!({"columns": 80, "tasks": [{"id": "t1", "name": "foo", "model": "claude-sonnet-5"}, {"name": "no-id"}, "not-a-dict"]});
        assert_eq!(
            render_all(&data),
            vec![("t1".to_string(), "foo (Sonnet)".to_string())]
        );
    }
}
