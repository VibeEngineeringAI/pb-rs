'use strict';
const releaseRoot = 'https://github.com/VibeEngineeringAI/pb-rs/releases/download/v0.1.0-rc.1';
const configurations = {
  macos: {
    command: `curl -fsSL ${releaseRoot}/install.sh | VERSION=0.1.0-rc.1 sh`,
    detail: 'Detects Apple Silicon or Intel automatically. Installs to ~/.local/bin.',
    shell: 'TERMINAL',
    pinned: `curl -fsSL https://github.com/VibeEngineeringAI/pb-rs/releases/download/v1.2.3/install.sh | VERSION=v1.2.3 sh`,
  },
  linux: {
    command: `curl -fsSL ${releaseRoot}/install.sh | VERSION=0.1.0-rc.1 sh`,
    detail: 'Detects x86-64 or ARM64 automatically. Installs to ~/.local/bin.',
    shell: 'TERMINAL',
    pinned: `curl -fsSL https://github.com/VibeEngineeringAI/pb-rs/releases/download/v1.2.3/install.sh | VERSION=v1.2.3 sh`,
  },
  windows: {
    command: `$env:VERSION = '0.1.0-rc.1'; try { irm ${releaseRoot}/install.ps1 | iex } finally { Remove-Item Env:VERSION -ErrorAction SilentlyContinue }`,
    detail: 'Installs for your Windows user. Open a new PowerShell window afterward.',
    shell: 'POWERSHELL',
    pinned: `$env:VERSION = 'v1.2.3'; try { irm https://github.com/VibeEngineeringAI/pb-rs/releases/download/v1.2.3/install.ps1 | iex } finally { Remove-Item Env:VERSION -ErrorAction SilentlyContinue }`,
  },
};
const tabs = [...document.querySelectorAll('[data-os]')];
function selectOS(os, focus = false) {
  const config = configurations[os];
  if (!config) return;
  for (const tab of tabs) {
    const selected = tab.dataset.os === os;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus();
  }
  document.getElementById('install-panel').setAttribute('aria-labelledby', `tab-${os}`);
  document.getElementById('install-command').textContent = config.command;
  document.getElementById('install-detail').textContent = config.detail;
  document.getElementById('shell-label').textContent = config.shell;
  document.getElementById('install-instruction').textContent = os === 'windows' ? 'Paste this into PowerShell.' : 'Paste this into your terminal.';
  document.getElementById('pinned-command').textContent = config.pinned;
}
for (const [index, tab] of tabs.entries()) {
  tab.addEventListener('click', () => selectOS(tab.dataset.os));
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    selectOS(tabs[next].dataset.os, true);
  });
}
const platform = navigator.userAgentData?.platform || navigator.platform || navigator.userAgent;
if (/win/i.test(platform)) selectOS('windows');
else if (/linux/i.test(platform)) selectOS('linux');
let feedbackTimer;
function feedback(message) {
  const status = document.getElementById('copy-status');
  status.textContent = message;
  status.classList.add('visible');
  clearTimeout(feedbackTimer);
  feedbackTimer = setTimeout(() => status.classList.remove('visible'), 4000);
}
function legacyCopy(value) {
  const field = document.createElement('textarea');
  field.value = value;
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.append(field);
  field.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch { /* Select the visible command below. */ }
  field.remove();
  return copied;
}
for (const button of document.querySelectorAll('[data-copy]')) {
  button.addEventListener('click', async () => {
    const command = document.getElementById(button.dataset.copy);
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(command.textContent);
        copied = true;
      }
    } catch { /* Clipboard permissions can vary; try the local fallback. */ }
    if (!copied) copied = legacyCopy(command.textContent);
    button.focus();
    if (copied) feedback('Command copied. Ready for your terminal.');
    else {
      const range = document.createRange();
      range.selectNodeContents(command);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      feedback('Command selected. Press Ctrl+C or ⌘C to copy.');
    }
  });
}
