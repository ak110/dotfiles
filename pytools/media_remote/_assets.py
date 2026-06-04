"""PWAの静的資産（HTML・manifest・SVGアイコン）。"""

# Tabler iconsのdevice-speakerを白縁取り付きでPWAアイコン用に流用する。
# 単一SVGで192/512どちらのサイズ要件にも応えるためベクターのままmanifestに登録する。
ICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 24 24" fill="none"
  stroke="#4f46e5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <rect width="24" height="24" fill="#ffffff"/>
  <path d="M6 3m0 2a2 2 0 0 1 2 -2h8a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2z"/>
  <path d="M12 13m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0"/>
  <path d="M12 7l.01 0"/>
</svg>
"""


def build_manifest(base_path: str = "") -> dict[str, object]:
    """PWA manifestの辞書を返す。"""
    return {
        "name": "Media Remote",
        "short_name": "Remote",
        "start_url": f"{base_path}/",
        "scope": f"{base_path}/",
        "display": "standalone",
        "orientation": "portrait",
        "theme_color": "#4f46e5",
        "background_color": "#ffffff",
        "icons": [
            {
                "src": f"{base_path}/icon.svg",
                "sizes": "192x192 512x512 any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    }


# 単一HTML。トークンはCookieまたはクエリで認証され、ボタン押下はfetchで`/api/key/<name>`へPOSTする。
INDEX_HTML = """\
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Media Remote</title>
<link rel="icon" type="image/svg+xml" href="icon.svg">
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#4f46e5">
<style>
  html, body { height: 100%; margin: 0; background: #1f2937; color: #f9fafb;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  body { display: flex; flex-direction: column; padding: 16px; box-sizing: border-box; }
  h1 { font-size: 18px; margin: 0 0 12px; text-align: center; color: #c7d2fe; }
  #grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; flex: 1; }
  button {
    font-size: 20px; font-weight: 600; color: #f9fafb;
    background: #4338ca; border: 0; border-radius: 16px; padding: 24px 8px;
    cursor: pointer; touch-action: manipulation;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
  }
  button:active { background: #3730a3; transform: translateY(1px); }
  button.wide { grid-column: 1 / span 2; }
  #status { margin-top: 12px; min-height: 1.5em; text-align: center; font-size: 13px; color: #a5b4fc; }
</style>
</head>
<body>
<h1>Media Remote</h1>
<div id="grid">
  <button class="wide" data-key="play_pause">&#9654;&#10074;&#10074; Play / Pause</button>
  <button data-key="prev">&#9198; Prev</button>
  <button data-key="next">&#9197; Next</button>
  <button data-key="vol_down">&#128264; Vol -</button>
  <button data-key="vol_up">&#128266; Vol +</button>
  <button data-key="mute">&#128263; Mute</button>
  <button data-key="stop">&#9209; Stop</button>
</div>
<div id="status"></div>
<script>
const status = document.getElementById("status");
async function sendKey(name) {
  status.textContent = "";
  try {
    const res = await fetch("api/key/" + encodeURIComponent(name), { method: "POST" });
    if (!res.ok) status.textContent = "失敗: " + res.status;
  } catch (e) {
    status.textContent = "通信エラー: " + e;
  }
}
for (const btn of document.querySelectorAll("button[data-key]")) {
  btn.addEventListener("click", () => sendKey(btn.dataset.key));
}
</script>
</body>
</html>
"""
