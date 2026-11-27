import {readFailure} from "./common.js";
import {BASE_PATH, recoverRead, setDisplayedReadError, setUpdateError} from "./common.js";

const DATED = String.raw`\d{8}-\d{6}-\d{3,}\.md`;

const childDialogs = [];
let referenceSequence = 0;
let renderDetailView, openMainDetail, readEntry;
let legacyNames;
let legacyRequest;

export function entryPageUrl(entry) {
  const query = new URLSearchParams({filename: entry.filename});
  return `${BASE_PATH}/?${query.toString()}`;
}

export function configureWiReferences(render, open, read) {
  renderDetailView = render;
  openMainDetail = open;
  readEntry = read;
}

export function decorateWiReferences(container, {beforeLink, filename} = {}) {
  const text = container.textContent;
  if (!text.includes(".md")) return;
  const prepare = beforeLink && (() => {
    beforeLink?.();
    beforeLink = undefined;
  });
  const decorate = names => linkWiReferences(container,
    {filename, referenced_wi_names: names}, {beforeLink: prepare});
  decorate(legacyNames);
  if (legacyNames || !text.replace(new RegExp(DATED, "g"), "").includes(".md")) return;
  legacyRequest ??= recoverRead(document.body.dataset.screen, BASE_PATH + "/api/wi-reference-names").then(async response => {
    if (!response.ok) throw new Error(String(response.status));
    const result = await response.json();
    return Array.isArray(result.names) ? result.names.filter(name => typeof name === "string") : [];
  }).then(names => (legacyNames = names)).catch(() => []).finally(() => { legacyRequest = undefined; });
  void legacyRequest.then(names => {
    if (container.isConnected && container.textContent === text && names.length) decorate(names);
  });
}

export function wiLink(filename) {
  const link = document.createElement('a');
  link.href = entryPageUrl({filename});
  link.textContent = filename;
  link.dataset.wiReferenceName = filename;
  link.addEventListener('click', event => {
    if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    void openReferencedWi(filename, link);
  });
  link.addEventListener("auxclick", event => {
    if (event.button !== 1) return;
    event.preventDefault();
    window.open(link.href, "_blank", "noopener");
  });
  return link;
}

export function linkWiReferences(container, entry, {beforeLink, allowUnlistedDated = true} = {}) {
  const knownNames = (entry.referenced_wi_names ?? []).slice().sort((a, b) => b.length - a.length);
  const alternatives = knownNames.map(name => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (allowUnlistedDated) alternatives.push(DATED);
  if (!alternatives.length) return;
  const filenames = new RegExp(
    String.raw`(?<![A-Za-z0-9_.-])(?:${alternatives.join('|')})(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])`, 'gu'
  );
  if (!filenames.test(container.textContent)) return;
  filenames.lastIndex = 0;
  const surroundingLink = beforeLink ? container.closest('a') : null;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode: node => {
      const link = node.parentElement.closest('a');
      return link && link !== surroundingLink ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const fragment = document.createDocumentFragment();
    let end = 0;
    for (const match of node.data.matchAll(filenames)) {
      const filename = match[0];
      if (filename === entry.filename) continue;
      fragment.append(node.data.slice(end, match.index), wiLink(filename));
      end = match.index + filename.length;
    }
    if (!end) continue;
    fragment.append(node.data.slice(end));
    beforeLink?.();
    beforeLink = undefined;
    node.replaceWith(fragment);
  }
}

function closeReferencedWi() {
  const dialog = childDialogs.pop();
  if (dialog) {
    dialog.close();
    dialog.remove();
  }
}

export function closeWiReferences() {
  while (childDialogs.length) closeReferencedWi();
}

async function openReferencedWi(filename, origin) {
  const parent = origin.closest('dialog');
  const dialog = document.createElement('dialog');
  dialog.className = 'dialog-shell detail-dialog wi-reference-dialog';
  const title = document.createElement('h2');
  title.id = `wi-reference-title-${childDialogs.length + 1}`;
  title.textContent = filename;
  title.className = "page-heading";
  dialog.setAttribute('aria-labelledby', title.id);
  const header = document.createElement('header');
  header.className = 'dialog-header';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'dialog-close';
  close.autofocus = true;
  close.setAttribute('aria-label', '閉じる');
  close.textContent = '×';
  close.addEventListener('click', closeReferencedWi);
  header.append(title, close);
  const body = document.createElement('div');
  body.className = 'dialog-body';
  body.tabIndex = 0;
  const loading = document.createElement('span');
  loading.className = 'inline-loading';
  loading.setAttribute('role', 'status');
  const loadingLabel = document.createElement('span');
  loadingLabel.className = 'visually-hidden';
  loadingLabel.textContent = '読み込み中';
  loading.append(loadingLabel);
  body.append(loading);
  const open = document.createElement('button');
  open.type = 'button';
  open.textContent = 'このWIを開く';
  open.className = 'button-primary';
  open.hidden = true;
  const footer = document.createElement('footer');
  footer.className = 'dialog-footer form-actions';
  footer.append(open);
  const frame = document.createElement('article');
  frame.className = 'dialog-frame';
  frame.append(header, body, footer);
  dialog.append(frame);
  const screen = document.body.dataset.screen;
  const key = `reference:${++referenceSequence}`;
  dialog.addEventListener('close', () => {
    setUpdateError(screen, key);
    const index = childDialogs.indexOf(dialog);
    if (index !== -1) childDialogs.splice(index, 1);
    dialog.remove();
    if (!origin.isConnected) {
      const scope = parent ?? document.getElementById(`screen-${document.body.dataset.screen}`);
      scope?.querySelector(`[data-wi-reference-name="${CSS.escape(filename)}"]`)?.focus({preventScroll: true});
    }
  });
  if (parent) parent.after(dialog);
  else {
    let layer = document.querySelector(".wi-reference-layer");
    if (!layer) {
      layer = document.createElement("div");
      layer.className = "wi-reference-layer";
      document.body.append(layer);
    }
    layer.append(dialog);
  }
  const bounds = parent?.getBoundingClientRect();
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const narrow = window.matchMedia('(max-width: 768px)').matches;
  const shift = bounds && !narrow && bounds.width - rem >= 32 * rem && bounds.height - rem >= 20 * rem ? rem : 0;
  if (bounds) Object.assign(dialog.style, {
    left: `${bounds.left + shift}px`, top: `${bounds.top + shift}px`,
    width: `${bounds.width - shift}px`, height: `${bounds.height - shift}px`,
  });
  childDialogs.push(dialog);
  dialog.showModal();
  async function read() {
    if (!dialog.open) return;
    try {
      const {entry} = await readEntry(filename);
      if (!dialog.open || document.body.dataset.screen !== screen) return;
      setUpdateError(screen, key);
      const view = document.getElementById('detail-view-template').content.cloneNode(true);
      const parts = new Map();
      for (const part of view.querySelectorAll('[id]')) {
        parts.set(part.id, part);
        part.removeAttribute('id');
      }
      renderDetailView(entry, id => parts.get(id), title);
      body.replaceChildren(view);
      open.hidden = false;
      open.addEventListener('click', () => {
        if (document.body.dataset.screen === 'wi') openMainDetail(entry);
        else location.assign(entryPageUrl({filename}));
      });
    } catch (error) {
      if (!dialog.open || document.body.dataset.screen !== screen) return;
      if (readFailure(error.status) !== "temporary") {
        setUpdateError(screen, key);
        body.textContent = error.status === 404 ? 'WIが見つかりません。' : error.message;
      } else {
        setDisplayedReadError(screen, key, 'WIの参照を取得できません。', read);
      }
    }
  }
  await read();
}
