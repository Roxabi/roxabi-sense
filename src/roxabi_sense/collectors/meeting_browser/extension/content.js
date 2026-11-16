// Runs in Chrome's isolated world. No page messaging bridge or page data is exported.
(() => {
  const tabId = crypto.randomUUID();
  const visible = (element) => {
    if (element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) === 0) {
        return false;
      }
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const enabled = (element) => !element.matches(':disabled') &&
    !element.closest('[aria-disabled="true"]');
  const hasControl = (labels, kinds) => labels.some((label) =>
    kinds.some((kind) => [...document.querySelectorAll(`${kind}[aria-label="${label}"]`)]
      .some((element) => visible(element) && enabled(element))));
  const sample = () => {
    // These specific controls are UI evidence, not proof of server membership.
    const buttons = ['button', '[role="button"]'];
    const joined = hasControl(['Leave call', "Quitter l'appel", 'Quitter la réunion'], buttons);
    const prejoin = hasControl(['Join now', 'Ask to join', 'Participer maintenant',
      'Demander à participer'], buttons);
    const home = hasControl(['New meeting', 'Nouvelle réunion'], buttons) &&
      hasControl(['Enter a code or link', 'Enter a code or nickname',
        'Saisissez un code ou un alias'], ['input', '[role="textbox"]']);
    const reconnecting = [...document.querySelectorAll('[role="alert"], [role="status"]')]
      .some((element) => visible(element) && /\b(?:reconnecting|reconnexion)\b/i.test(element.textContent || ''));
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
