// Runs in Chrome's isolated world. No page messaging bridge or page data is exported.
(() => {
  const tabId = crypto.randomUUID();
  const visible = (selector) => [...document.querySelectorAll(selector)].some((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility === 'visible' &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0 &&
      !element.closest('[hidden], [inert], [aria-hidden="true"]');
  });
  const sample = () => {
    // Exact accessible labels are intentionally conservative. Other languages and
    // redesigned, ambiguous or reconnecting interfaces are not evidence.
    const joined = visible('button[aria-label="Leave call"], [role="button"][aria-label="Leave call"]');
    const prejoin = visible('button[aria-label="Join now"], button[aria-label="Ask to join"]');
    const home = visible('[aria-label="New meeting"]') &&
      visible('[aria-label="Enter a code or link"]');
    const reconnecting = [...document.querySelectorAll('[role="alert"], [role="status"]')]
      .some((element) => /\breconnecting\b/i.test(element.textContent || ''));
    if (reconnecting || Number(joined) + Number(prejoin) + Number(home) !== 1) {
      return {state: 'unknown', basis: 'unrecognized'};
    }
    if (joined) return {state: 'joined', basis: 'leave_control'};
    if (prejoin) return {state: 'not_joined', basis: 'prejoin'};
    return {state: 'not_joined', basis: 'home'};
  };
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || typeof message?.nonce !== 'string') return;
    if (message.kind === 'meeting-confirm') {
      sendResponse({kind: 'meeting-confirmed', nonce: message.nonce});
      return;
    }
    if (message.kind !== 'meeting-sample') return;
    chrome.runtime.sendMessage({kind: 'meeting-observation', nonce: message.nonce, tab_id: tabId, ...sample()}).catch(() => {});
  });
})();
