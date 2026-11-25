self.addEventListener('push', event => {
  let payload;
  try {
    payload = event.data?.json();
  } catch {
  }
  event.waitUntil(self.registration.showNotification(
    typeof payload?.title === 'string' ? payload.title : 'atk serveに新しい確認がある',
    {
      body: typeof payload?.body === 'string' ? payload.body : '',
      data: {url: typeof payload?.url === 'string' ? payload.url : './'}
    }
  ));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const scope = new URL(self.registration.scope);
  const target = new URL(scope);
  try {
    const candidate = new URL(event.notification.data?.url, scope);
    if (candidate.origin === scope.origin && candidate.pathname === scope.pathname) {
      const filename = candidate.searchParams.get('filename');
      if (filename) target.searchParams.set('filename', filename);
    }
  } catch {
  }
  event.waitUntil(self.clients.openWindow(target.href));
});
