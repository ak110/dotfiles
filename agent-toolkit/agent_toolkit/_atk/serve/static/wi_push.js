import {BASE_PATH} from "./common.js";
import {api, setGlobalError} from "./wi_feedback.js";
import {byId} from "./wi_state.js";

const route = '/api/web-push';
let registration;
let publicKey;
let pushInitialization;
let busy = false;
const subscriptionRetry = 'WI画面を開き直し、購読状態を確認して再試行してください。';

function request(method, data) {
  return api(route, {method, body: JSON.stringify(data)});
}

async function syncButtons() {
  const subscribe = byId('push-subscribe-button');
  const unsubscribe = byId('push-unsubscribe-button');
  const subscription = await registration.pushManager.getSubscription();
  const saved = subscription && await isServerSubscribed(subscription);
  subscribe.hidden = Boolean(saved) || Notification.permission === 'denied';
  unsubscribe.hidden = !subscription;
}

async function isServerSubscribed(subscription) {
  const result = await request('POST', {endpoint: subscription.endpoint, check: true});
  return result.subscribed === true;
}

export function initializePush() {
  pushInitialization ??= setup().catch(() => setGlobalError('Web Pushを準備できませんでした。' + subscriptionRetry));
}

async function setup() {
  const subscribe = byId('push-subscribe-button');
  const unsubscribe = byId('push-unsubscribe-button');
  if (!subscribe || !unsubscribe || !window.isSecureContext ||
      !('PushManager' in window) || !('Notification' in window) || !('serviceWorker' in navigator)) return;
  const settings = await api(route);
  if (!settings.enabled) return;

  const installed = await navigator.serviceWorker.register(`${BASE_PATH}/wi-push-worker.js`);
  const ready = await navigator.serviceWorker.ready;
  if (ready.scope !== installed.scope) return;
  registration = ready;
  publicKey = settings.public_key;
  subscribe.addEventListener('click', () => { void changeSubscription(false); });
  unsubscribe.addEventListener('click', () => { void changeSubscription(true); });
  await syncButtons();
}

async function changeSubscription(remove) {
  if (busy) return;
  const buttons = [byId('push-subscribe-button'), byId('push-unsubscribe-button')];
  busy = true;
  buttons.forEach(button => { button.disabled = true; });
  try {
    if (remove) {
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await request('DELETE', {endpoint: subscription.endpoint});
        await subscription.unsubscribe();
      }
    } else {
      // 許可を求める操作は、クリックのユーザー操作が有効なうちに開始する。 pyfltr: allow-comment
      const subscription = await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: publicKey});
      await request('POST', subscription.toJSON());
    }
  } catch (error) {
    const failed = remove ? 'Web Pushの購読を解除できませんでした。' : 'Web Pushを購読できませんでした。';
    const next = !remove && (Notification.permission === 'denied' || error?.name === 'NotAllowedError')
      ? 'ブラウザーのこのサイトの通知設定で許可してから、もう一度購読してください。'
      : subscriptionRetry;
    setGlobalError(failed + next);
  } finally {
    try {
      await syncButtons();
    } catch {
      setGlobalError('Web Pushの購読状態を取得できませんでした。' + subscriptionRetry);
    }
    buttons.forEach(button => { button.disabled = false; });
    busy = false;
  }
}

export async function pushHandlesNotifications() {
  if (!registration) return false;
  try {
    const subscription = await registration.pushManager.getSubscription();
    return Boolean(subscription && await isServerSubscribed(subscription));
  } catch {
    return false;
  }
}
