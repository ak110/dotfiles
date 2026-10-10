// modが会話へ渡す本文の境界を共有する。本文自身は変更せず、属性値だけをXML用に引用する。
function attribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll("\r", "&#13;").replaceAll("\n", "&#10;").replaceAll("\t", "&#9;");
}

export function automatedMessage(source: string, kind: string, body: string): string {
  return `<atk-auto source="${attribute(source)}" kind="${attribute(kind)}">\n${body}\n</atk-auto>`;
}
