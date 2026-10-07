//! statusline系の各行（subagentStatusLineとagents_serverのsession行）が共有する描画補助。
//!
//! 表示幅の計算、省略記号付きの切り詰め、経過時間の整形と、名前列・説明・右寄せ要素の
//! 共通レイアウトを持つ。行の内容を組み立てる各モジュールは、ここから同じレイアウトを使う。

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use unicode_width::UnicodeWidthChar;

pub(crate) const SEP: &str = " · ";
pub(crate) const ELLIPSIS: &str = "…";
const GAP_MIN: usize = 2;
pub(crate) const DEFAULT_COLUMNS: usize = 80;

/// 名前列・説明・右寄せ要素を、端末幅内の共通レイアウトへ整形する。
pub(crate) fn render_line(
    name_column: &str,
    description: &str,
    right_parts: &[String],
    width: usize,
    name_width: usize,
) -> String {
    let name_present = name_width > 0;
    let padded_name = if name_present {
        let fitted_name = truncate(name_column, name_width);
        let pad = name_width.saturating_sub(display_width(&fitted_name));
        format!("{fitted_name}{}", " ".repeat(pad))
    } else {
        String::new()
    };

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
        truncate(description, desc_budget)
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
    if display_width(&line) > width {
        truncate(&line, width)
    } else {
        line
    }
}

/// `startTime`からの経過時間を`1h23m`・`4m56s`・`45s`形式へ整形する。解釈不能・未来時刻はNone。
pub(crate) fn format_elapsed(start_time: Option<&Value>, now: DateTime<Utc>) -> Option<String> {
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
            // オフセット省略のnaive ISO 8601も許容する（オフセットを省略した値をUTCとして扱うPython側のfromisoformatの解釈に合わせる）。
            NaiveDateTime::parse_from_str(&normalized, "%Y-%m-%dT%H:%M:%S%.f")
                .ok()
                .map(|naive| naive.and_utc())
        }
        _ => None,
    }
}

/// `description`内の改行を空白へ置換し、連続する空白を1個へ畳んで1行化する。
pub(crate) fn normalize_description(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// 対象端末の描画に合わせ、曖昧幅を1セルとして表示幅を返す。
///
/// 日本語の全角文字は2セル、区切り記号や省略記号などの曖昧幅は1セルとして描画される。
pub(crate) fn display_width(text: &str) -> usize {
    text.chars().map(|c| c.width().unwrap_or(1)).sum()
}

/// 文字列を表示幅`budget`セル以内へ省略記号付きで切り詰める。
///
/// 省略記号`…`（U+2026）は対象端末で1セルを占めるため、`display_width`の実測幅を予約する。
pub(crate) fn truncate(text: &str, budget: usize) -> String {
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
        let w = ch.width().unwrap_or(1);
        if used + w > budget - ellipsis_width {
            break;
        }
        result.push(ch);
        used += w;
    }
    result.push_str(ELLIPSIS);
    result
}
