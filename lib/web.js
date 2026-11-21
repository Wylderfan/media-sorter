'use strict';
const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('media-sorter-token') || '';
if (token) sessionStorage.setItem('media-sorter-token', token);
history.replaceState(null, '', '/');
let state = null, pending = false, shownPrompt = null;
async function api(path, body) {
  const response = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {'X-Portal-Token': token, 'Content-Type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function render(next) {
  state = next;
  const active = state && state.code === null;
  document.querySelectorAll('[data-media]').forEach(button => button.disabled = pending || active);
  $('apply').disabled = pending || active;
  $('cancel').disabled = pending || !active || state.cancelled;
  $('status').textContent = !state ? 'Ready to preview' : active ? (state.cancelled ? 'Cancelling…' : state.prompt ? 'Waiting for your answer' : 'Working…') : state.cancelled ? 'Batch cancelled' : state.code === 0 ? 'Batch finished — review the summary' : 'Batch stopped — review the errors';
  if (state) {
    const output = $('output');
    const atEnd = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
    output.textContent = state.output || 'Starting CLI…';
    if (atEnd) output.scrollTop = output.scrollHeight;
    $('trimmed').hidden = !state.trimmed;
  }
  const prompt = active && !state.cancelled && state.prompt;
  $('reply').hidden = !prompt;
  if (prompt) {
    $('prompt').textContent = prompt.text;
    const key = state.id + ':' + prompt.id;
    if (shownPrompt !== key) { $('answer').value = ''; shownPrompt = key; $('answer').focus(); }
  }
  $('reply').querySelectorAll('input,button').forEach(el => el.disabled = pending);
}
async function action(path, body) {
  pending = true; $('error').textContent = ''; render(state);
  try { render(await api(path, body)); }
  catch (error) { $('error').textContent = error.message; }
  finally { pending = false; render(state); }
}
document.querySelectorAll('[data-media]').forEach(button => button.onclick = () => {
  const apply = $('apply').checked;
  if (apply && !confirm('Start APPLY for ' + button.dataset.media + '? Approved items will be copied to the configured destination. Review every CLI destination before answering Yes.')) return;
  action('start', {media: button.dataset.media, apply, confirm: apply});
});
$('reply').onsubmit = event => { event.preventDefault(); if (state && state.prompt && !pending) action('answer', {job: state.id, prompt: state.prompt.id, answer: $('answer').value}); };
$('eof').onclick = () => { if (state && state.prompt) action('answer', {job: state.id, prompt: state.prompt.id, answer: null}); };
$('cancel').onclick = () => { if (state) action('cancel', {job: state.id}); };
async function poll() {
  if (!pending) {
    try { render(await api('state')); }
    catch (error) { $('error').textContent = 'Connection unavailable: ' + error.message + '. Reopen the link printed by the server if needed.'; }
  }
  setTimeout(poll, 500);
}
poll();
