// Focus tracker bridge: reports the focused tab to the local agent.
// The URL is sent only while a Brave window has OS focus. Each report also lists
// the active-tab titles of all normal windows (titles Windows already shows), so
// the agent can tell a private window apart and keep it out of screenshots.
// Never stores anything itself, never runs in private windows.

const ENDPOINT = "http://127.0.0.1:5610/browser";
const HEARTBEAT_MINUTES = 0.5; // also keeps "audible" fresh while you watch a lecture

async function snapshot() {
  const wins = await chrome.windows.getAll({ populate: true, windowTypes: ["normal", "app", "popup"] });
  const info = { windowTitles: [] };
  for (const win of wins) {
    if (win.incognito) continue;
    const tab = (win.tabs || []).find((t) => t.active);
    if (!tab) continue;
    info.windowTitles.push(tab.title || "");
    if (win.focused) {
      info.focused = {
        url: tab.url || "",
        title: tab.title || "",
        audible: Boolean(tab.audible),
        tabCount: (win.tabs || []).length,
      };
    }
  }
  return info;
}

async function report() {
  try {
    const info = await snapshot();
    await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(info),
    });
  } catch (_err) {
    // Agent not running: nothing to do.
  }
}

chrome.tabs.onActivated.addListener(report);
chrome.tabs.onUpdated.addListener((_id, change, tab) => {
  if (tab.active && (change.url || change.title || "audible" in change)) report();
});
chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId !== chrome.windows.WINDOW_ID_NONE) report();
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "heartbeat") report();
});

function ensureAlarm() {
  chrome.alarms.get("heartbeat", (existing) => {
    if (!existing) chrome.alarms.create("heartbeat", { periodInMinutes: HEARTBEAT_MINUTES });
  });
}
chrome.runtime.onInstalled.addListener(ensureAlarm);
chrome.runtime.onStartup.addListener(() => {
  ensureAlarm();
  report();
});
