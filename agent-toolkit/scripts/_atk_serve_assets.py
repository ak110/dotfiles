"""`atk serve`の自己完結型フロントエンド資産。"""

# ruff: noqa: E501

HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>agent-toolkit feedback</title><link rel="stylesheet" href="__BASE_PATH_HTML__/static/app.css"></head>
<body><header><h1>Feedback / TBD</h1><span id="enabled"></span><button id="toggle"></button><button id="refresh">再読込</button></header>
<section aria-label="フィルター"><label>種別<select id="type"><option value="all">すべて</option><option value="feedback">Feedback</option><option value="tbd">TBD</option></select></label>
<label>状態<select id="status"><option value="active">未処理</option><option value="all">すべて</option><option>inbox</option><option>processing</option><option>adopted</option><option>rejected</option></select></label>
<label>回答<select id="answered"><option value="all">すべて</option><option value="yes">回答済み</option><option value="no">未回答</option></select></label>
<label>対象<input id="target_repo"></label><label>カテゴリ<input id="category"></label><label>投入元<input id="source"></label></section>
<nav><button id="new-feedback">Feedback追加</button><button id="new-tbd">TBD追加</button><button data-action="start-processing">処理開始</button><button data-action="adopt">採用</button><button data-action="reject">不採用</button><button data-action="remove">削除</button><button id="commit">外部編集をcommit</button>
<label>注記<input id="batch-note"></label><label>カテゴリ<input id="batch-category"></label><label>commit<input id="batch-commit"></label></nav>
<main><section><label><input type="checkbox" id="select-all">すべて選択</label><ul id="entries"></ul></section>
<article><dl id="metadata"></dl><pre id="detail"></pre><textarea id="editor" aria-label="本文"></textarea><button id="save">本文を保存</button>
<section id="answer-panel"><textarea id="answer" aria-label="TBD回答"></textarea><button id="answer-save">回答を保存</button></section></article></main>
<dialog id="new-entry"><form><h2 id="dialog-title"></h2><label>本文<textarea name="message" required></textarea></label>
<label>対象リポジトリ<input name="target_repo" required></label><label>投入元<input name="source"></label><label id="scope-row">スコープ<input name="scope"></label>
<label id="question-row">質問形式<select name="question_type"><option value="free-form">自由記述</option><option value="yes-no">はい／いいえ</option><option value="choice">選択式</option></select></label>
<label id="choices-row">選択肢（1行1件）<textarea name="choices"></textarea></label><button type="submit">保存</button><button type="button" id="cancel-dialog">中止</button></form></dialog>
<p id="message" role="status"></p><p id="error" role="alert"></p><script src="__BASE_PATH_HTML__/static/app.js"></script></body></html>"""

CSS = """body{font-family:system-ui,sans-serif;max-width:1200px;margin:auto;padding:1rem;color:#202124;background:#fafafa}
header,nav,body>section,main{display:flex;gap:.75rem;flex-wrap:wrap;align-items:center}header{justify-content:space-between}
main{margin-top:1rem;align-items:start}main>section{min-width:22rem;flex:1}article{flex:2;min-width:22rem}
ul{padding:0;list-style:none}li{display:grid;grid-template-columns:auto 1fr;gap:.5rem;padding:.5rem;border-bottom:1px solid #ddd}
li button{text-align:left}.summary{display:block;color:#555;font-size:.9rem}button,input,select,textarea{font:inherit;padding:.5rem}
textarea{box-sizing:border-box;width:100%;min-height:10rem}dialog{max-width:36rem;width:90%}dialog label{display:block;margin:.5rem 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:.75rem}#metadata{display:grid;grid-template-columns:max-content 1fr;gap:.25rem .75rem}
#error{color:#b00020;white-space:pre-wrap}#message{color:#176922}:focus-visible{outline:3px solid #1769aa;outline-offset:2px}
button:disabled{opacity:.45}#answer-panel[hidden],#editor[hidden],#save[hidden]{display:none}
@media(max-width:700px){main{display:block}main>section,article{min-width:0}nav button{flex:1 1 9rem}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}"""

JS = """const BASE_PATH=__BASE_PATH_JS__;const $=s=>document.querySelector(s);let entries=[],current=null,newKind='feedback';
async function api(path,options={}){const o={...options,headers:{'Content-Type':'application/json',...(options.headers||{})}};const r=await fetch(BASE_PATH+path,o);const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(`${r.status}: ${j.error||r.statusText}`);return j}
function showError(error){$('#error').textContent=error instanceof Error?error.message:String(error);$('#message').textContent=''}
function success(text){$('#message').textContent=text;$('#error').textContent=''}
function selected(){return [...document.querySelectorAll('#entries input:checked')].map(input=>entries[Number(input.dataset.index)])}
function appendText(parent,label,value,className=''){const span=document.createElement('span');if(className)span.className=className;span.textContent=`${label}: ${value??'—'}`;parent.append(span)}
function renderEntry(e,index){const li=document.createElement('li'),check=document.createElement('input'),button=document.createElement('button');check.type='checkbox';check.dataset.index=String(index);check.setAttribute('aria-label',`${e.filename}を選択`);for(const [label,value] of [['kind',e.kind],['filename',e.filename],['state',e.state],['target_repo',e.target_repo],['source',e.source],['category',e.category],['answered',e.answered],['summary',e.summary],['updated_at',e.updated_at]])appendText(button,label,value,label==='summary'?'summary':'');button.onclick=()=>detail(e);li.append(check,button);return li}
function render(){const items=entries.map(renderEntry);$('#entries').replaceChildren(...items);updateActions()}
function actionAllowed(action,e){const active=['inbox','processing'].includes(e.state);return action==='start-processing'?e.state==='inbox':active}
function updateActions(){const chosen=selected();document.querySelectorAll('[data-action]').forEach(button=>{button.disabled=!chosen.length||!chosen.every(e=>actionAllowed(button.dataset.action,e))});$('#batch-category').disabled=!(chosen.length&&chosen.every(e=>e.kind==='feedback'))}
async function status(){const s=await api('/api/status');$('#enabled').textContent=s.enabled?'有効':'無効';$('#toggle').textContent=s.enabled?'無効化':'有効化';$('#toggle').dataset.enabled=String(s.enabled)}
async function load(){try{const q=new URLSearchParams();for(const k of ['type','status','answered','target_repo','category','source']){const v=$('#'+k).value;if(v)q.set(k,v)}entries=(await api('/api/entries?'+q)).entries;render();await status()}catch(error){showError(error)}}
async function detail(e){try{const item=(await api(`/api/entries/${e.state}/${encodeURIComponent(e.filename)}`)).entry;current=item;$('#detail').textContent=item.content;$('#editor').value=item.content;$('#answer').value='';const editable=['inbox','processing'].includes(item.state);$('#editor').hidden=!editable;$('#save').hidden=!editable;$('#answer-panel').hidden=!(item.kind==='tbd'&&['inbox','processing'].includes(item.state)&&!item.answered);$('#metadata').replaceChildren(...['target_repo','source','category','summary','updated_at'].flatMap(k=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=item[k]||'—';return[dt,dd]}));$('#editor').focus()}catch(error){showError(error)}}
async function mutate(path,body,message){try{await api(path,{method:'POST',body:JSON.stringify(body)});success(message);await load()}catch(error){showError(error);throw error}}
$('#refresh').onclick=load;for(const k of ['type','status','answered','target_repo','category','source'])$('#'+k).onchange=load;
$('#entries').onchange=updateActions;$('#select-all').onchange=e=>{document.querySelectorAll('#entries input').forEach(input=>{input.checked=e.target.checked});updateActions()};
$('#toggle').onclick=()=>mutate($('#toggle').dataset.enabled==='true'?'/api/disable':'/api/enable',{},'設定を更新しました');
$('#save').onclick=async()=>{if(!current)return;try{await api(`/api/entries/${current.state}/${encodeURIComponent(current.filename)}`,{method:'PUT',body:JSON.stringify({content:$('#editor').value})});success('本文を保存しました');await load()}catch(error){showError(error)}};
$('#answer-save').onclick=async()=>{if(!current||!$('#answer').value.trim())return;try{await mutate('/api/entries/answer',{filename:current.filename,answer:$('#answer').value},'回答を保存しました');$('#answer').value=''}catch(error){}};
function batchBody(action,items){const body={filenames:items.map(e=>e.filename)},note=$('#batch-note').value.trim(),category=$('#batch-category').value.trim(),commit=$('#batch-commit').value.trim();if(note&&['adopt','reject','remove'].includes(action))body.note=note;if(category&&action==='adopt'&&items.every(e=>e.kind==='feedback'))body.category=category;if(commit&&['adopt','reject'].includes(action))body.commit=commit;if(action==='remove'&&items.some(e=>e.state==='processing'))body.force=true;return body}
document.querySelectorAll('[data-action]').forEach(button=>button.onclick=async()=>{const chosen=selected(),action=button.dataset.action,filenames=chosen.map(e=>e.filename);if(!filenames.length)return;const forcing=action==='remove'&&chosen.some(e=>e.state==='processing');const warning=forcing?'（処理中エントリを含むため強制削除します）\\n':'';if(!confirm(`${button.textContent}（${action}）を次の対象へ実行しますか:\\n${warning}${filenames.join('\\n')}`))return;try{await api(`/api/entries/${action}`,{method:'POST',body:JSON.stringify(batchBody(action,chosen))});for(const id of ['batch-note','batch-category','batch-commit'])$('#'+id).value='';success(`${button.textContent}を実行しました`);await load()}catch(error){showError(error)}});
$('#commit').onclick=()=>confirm('外部編集をcommitしますか')&&mutate('/api/entries/commit',{},'commitしました');
function openDialog(kind){newKind=kind;$('#dialog-title').textContent=kind==='feedback'?'Feedback追加':'TBD追加';for(const id of ['scope-row','question-row','choices-row'])$('#'+id).hidden=kind==='feedback';const targetInput=document.querySelector('#new-entry [name="target_repo"]');if(!targetInput.value)targetInput.value=$('#target_repo').value;$('#new-entry').showModal()}
$('#new-feedback').onclick=()=>openDialog('feedback');$('#new-tbd').onclick=()=>openDialog('tbd');$('#cancel-dialog').onclick=()=>$('#new-entry').close();
$('#new-entry form').onsubmit=async event=>{event.preventDefault();const form=new FormData(event.target),body={type:newKind,messages:[form.get('message')]},target=String(form.get('target_repo')||'').trim(),source=String(form.get('source')||'').trim();if(target)body.target_repo=target;if(source)body.source=source;if(newKind==='tbd'){body.scope=String(form.get('scope')||'').trim();body.question_type=form.get('question_type');if(body.question_type==='choice')body.choices=String(form.get('choices')||'').split('\\n').map(x=>x.trim()).filter(Boolean)}try{await api('/api/entries',{method:'POST',body:JSON.stringify(body)});event.target.reset();$('#new-entry').close();success('追加しました');await load()}catch(error){showError(error)}};
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key==='s'&&!$('#save').hidden){event.preventDefault();$('#save').click()}if(event.key==='Escape'&&$('#new-entry').open)$('#new-entry').close()});
const events=new EventSource(BASE_PATH+'/api/events');events.addEventListener('changed',load);events.onerror=()=>setTimeout(load,1000);load();"""
