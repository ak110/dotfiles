const paths = new Set(["/api/events", "/api/plans/events", "/api/sessions/events"]);
const eventNames = ["open", "heartbeat", "subscribed", "message", "changed", "sync-error", "sync-ok"];
const streams = new Map();

function createStream(path, limit) {
  const clients = new Set();
  const url = new URL(".." + path, self.location.href);
  let source;
  let retry;
  let lastSeen = 0;
  let failures = 0;
  let subscribed = false;
  const broadcast = (name, data = "") => {
    for (const client of clients) client.postMessage([name, data]);
  };

  function disconnect() {
    source?.close();
    source = undefined;
    subscribed = false;
  }

  function connected() {
    clearTimeout(retry);
    retry = undefined;
    disconnect();
    lastSeen = Date.now();
    const current = new EventSource(url);
    source = current;
    for (const name of eventNames) {
      current.addEventListener(name, event => {
        if (source !== current) return;
        lastSeen = Date.now();
        if (name === "open") failures = 0;
        if (name === "subscribed") subscribed = true;
        broadcast(name, event.data ?? "");
      });
    }
    current.addEventListener("error", () => {
      if (source === current) unavailable();
    });
  }

  function unavailable() {
    disconnect();
    failures += 1;
    broadcast("error");
    retry = setTimeout(connected, Math.min(30_000, 1500 * 2 ** Math.min(5, failures - 1)));
  }

  const timer = setInterval(() => {
    if (source && Date.now() - lastSeen > limit) unavailable();
  }, Math.max(100, Math.min(5000, limit / 3)));
  connected();
  return {
    attach(port) {
      clients.add(port);
      if (subscribed) port.postMessage(["available", ""]);
    },
    detach(port) {
      clients.delete(port);
      if (clients.size) return;
      disconnect();
      clearTimeout(retry);
      clearInterval(timer);
      streams.delete(path);
    },
    reconnect: connected,
  };
}

self.addEventListener("connect", ({ports: [port]}) => {
  let stream;
  function detach() {
    const active = stream;
    stream = undefined;
    active?.detach(port);
  }
  port.addEventListener("message", ({data: request}) => {
    if (!Array.isArray(request)) return;
    const [action, path, limit] = request;
    if (action === "close") {
      detach();
      port.close();
    }
    else if (action === "retry") stream?.reconnect();
    else if (action === "subscribe" && !stream && paths.has(path) && Number.isFinite(limit) && limit > 0) {
      if (!streams.has(path)) streams.set(path, createStream(path, limit));
      stream = streams.get(path);
      stream.attach(port);
    }
  });
  port.addEventListener("close", detach, {once: true});
  port.start();
});
