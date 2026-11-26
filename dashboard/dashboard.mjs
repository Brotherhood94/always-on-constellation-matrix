const resources = { cpu: 'CPU', memory: 'Memory', disk: 'Disk' };

export function httpsLink(value) {
  if (typeof value !== 'string') throw new TypeError('Expected an HTTPS link');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) {
    throw new TypeError('Use an HTTPS URL without credentials, parameters, or fragments');
  }
  if (url.search) {
    const values = [...url.searchParams];
    if (values.length !== 1 || values[0][0] !== 'connectURL') throw new TypeError('Unexpected URL parameter');
    const graph = new URL(values[0][1]);
    if (graph.protocol !== 'bolt+s:' || graph.hostname !== url.hostname || graph.username ||
        graph.password || graph.search || graph.hash || graph.pathname || !graph.port) {
      throw new TypeError('Unexpected Neo4j connection URL');
    }
  }
  return url.href;
}

export function freshness(timestamp, now = Date.now()) {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return 'Unavailable';
  const label = now / 1000 - timestamp > 180 ? 'Stale since' : 'Updated';
  return `${label} ${new Date(timestamp * 1000).toLocaleString()}`;
}

export function historyWithin(history, resource, days, now = Date.now()) {
  const collectorNames = { cpu: 'CPU', memory: 'RAM', disk: 'Disk' };
  const rows = history?.[resource] ?? history?.[collectorNames[resource]];
  return (Array.isArray(rows) ? rows : []).filter(row =>
    Array.isArray(row) && Number.isFinite(row[0]) && Number.isFinite(row[1]) &&
    row[0] >= now / 1000 - days * 86400 && row[0] <= now / 1000 + 60,
  ).sort((a, b) => a[0] - b[0]);
}

function draw(canvas, samples, days, now) {
  const context = canvas.getContext('2d');
  const style = getComputedStyle(canvas);
  const { width, height } = canvas;
  const left = 36;
  const span = days * 86400;
  const start = now / 1000 - span;
  const range = Math.max(100, ...samples.map(row => row[1]));
  context.font = '12px system-ui';
  for (const value of [0, range / 2, range]) {
    const y = 10 + (1 - value / range) * (height - 24);
    context.fillStyle = style.getPropertyValue('--muted');
    context.fillText(`${Math.round(value)}%`, 0, y + 4);
    context.strokeStyle = style.getPropertyValue('--border');
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(width, y);
    context.stroke();
  }
  context.strokeStyle = style.getPropertyValue('--accent');
  context.lineWidth = 2;
  context.beginPath();
  samples.forEach(([time, value], index) => {
    const x = left + Math.min(1, (time - start) / span) * (width - left - 2);
    const y = 10 + (1 - Math.max(0, value) / range) * (height - 24);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
    if (samples.length === 1) context.arc(x, y, 2, 0, 2 * Math.PI);
  });
  context.stroke();
}

function profileLink(agent, title) {
  const link = document.createElement('a');
  link.href = '#/agents/' + encodeURIComponent(agent.key);
  link.textContent = title;
  link.setAttribute('aria-label', title === 'Persona and duties' ? title + ' for ' + agent.name : title);
  return link;
}

function showPersona(agents) {
  const view = document.getElementById('persona-view');
  const key = /^#\/agents\/([a-z0-9_-]+)$/u.exec(location.hash)?.[1];
  view.hidden = !key;
  document.getElementById('overview').hidden = Boolean(key);
  if (!key) return;
  const back = document.createElement('a');
  back.href = '#agents-heading';
  back.textContent = '← Agent roster';
  view.replaceChildren(back);
  const write = (tag, value) => {
    const node = document.createElement(tag);
    node.textContent = value;
    view.append(node);
    return node;
  };
  const agent = agents.find(item => item.key === key);
  if (!agent) { write('h2', 'Agent profile unavailable'); return; }
  write('h2', agent.name);
  write('p', agent.persona?.purpose || agent.limits || 'Profile awaiting registration.');
  write('h3', 'Duties');
  const duties = write('ul', '');
  for (const value of agent.persona?.duties ?? agent.capabilities?.map(item => item.description) ?? []) {
    const li = document.createElement('li');
    li.textContent = value;
    duties.append(li);
  }
  for (const [label, value] of [
    ['Working style', agent.persona?.working_style], ['Limits', agent.limits],
    ['Enabled tools', agent.tools?.join(', ') || 'None registered'],
    ['Applied persona revision', agent.revision ?? 'Starter description'],
  ]) { write('h3', label); write('p', value ?? 'Unavailable'); }
  write('h3', 'Can contact');
  const contacts = (agent.contacts ?? []).map(id => agents.find(item => item.key === id)).filter(Boolean);
  if (!contacts.length) write('p', 'No registered contacts');
  else {
    const list = write('ul', '');
    for (const contact of contacts) {
      const li = document.createElement('li');
      li.append(profileLink(contact, contact.name));
      list.append(li);
    }
  }
}

function render(metrics, catalog, links, integrationStates) {
  const now = Date.now();
  const el = id => document.getElementById(id);
  const make = (tag, text, className) => Object.assign(document.createElement(tag), { textContent: text, className });
  el('freshness').textContent = freshness(metrics?.time, now);
  el('agent-freshness').textContent = freshness(catalog?.time ?? metrics?.time, now);
  try {
    el('context-link').href = httpsLink(links?.context);
    el('context-link').hidden = false;
  } catch {
    el('context-link').removeAttribute('href');
    el('context-link').hidden = true;
  }
  el('resources').replaceChildren();
  const days = Number(el('range').value);
  for (const [id, label] of Object.entries(resources)) {
    const card = make('article', '', 'card');
    const samples = historyWithin(metrics?.history, id, days, now);
    const latest = samples.at(-1);
    card.append(make('h3', label, ''), make('p', latest ? `${latest[1].toFixed(1)}%` : '—', 'value'));
    if (latest) {
      const chart = document.createElement('canvas');
      chart.width = 440;
      chart.height = 114;
      chart.setAttribute('role', 'img');
      const values = samples.map(row => row[1]);
      chart.setAttribute('aria-label',
        `${label} usage over ${days} days. Minimum ${Math.min(...values).toFixed(1)}%, maximum ${Math.max(...values).toFixed(1)}%.`);
      card.append(chart);
      el('resources').append(card);
      draw(chart, samples, days, now);
    } else {
      card.append(make('p', 'No samples in this period', ''));
      el('resources').append(card);
    }
  }
  el('agents').replaceChildren();
  const agents = catalog?.agents ?? metrics?.agents ?? [];
  for (const agent of agents) {
    const row = document.createElement('tr');
    for (const value of [agent.name, agent.status, agent.server, [agent.model, agent.effort].filter(Boolean).join(' · ')]) {
      row.append(make('td', value || '—', ''));
    }
    const profile = make('td', '', '');
    if (agent.key) profile.append(profileLink(agent, 'Persona and duties'));
    row.append(profile);
    el('agents').append(row);
  }
  if (!agents.length) {
    const row = document.createElement('tr');
    const cell = make('td', 'No agent inventory available', '');
    cell.colSpan = 5;
    row.append(cell);
    el('agents').append(row);
  }
  showPersona(agents);
  const entries = Object.entries(integrationStates?.services ?? {});
  el('integrations').replaceChildren();
  for (const [name, state] of [...entries, ['Last successful backup', metrics?.lastBackup ?? 'Unavailable']]) {
    const row = document.createElement('div');
    row.append(make('dt', name, ''), make('dd', state, ''));
    el('integrations').append(row);
  }
  if (entries.length) {
    const row = document.createElement('div');
    row.append(make('dt', 'Integration data', ''), make('dd', freshness(integrationStates.time, now), ''));
    el('integrations').append(row);
  }
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error('Dashboard data unavailable');
  return response.json();
}

if (typeof document !== 'undefined') {
  let state = [];
  async function refresh() {
    const reports = await Promise.allSettled(['metrics', 'agents', 'links', 'integrations'].map(path => fetchJson(path + '.json')));
    state = reports.map(({ status, value }, index) => status === 'fulfilled' ? value : state[index]);
    render(...state);
  }

  document.getElementById('range').addEventListener('change', () => render(...state));
  window.addEventListener('hashchange', () => showPersona(state[1]?.agents ?? state[0]?.agents ?? []));
  void refresh();
  setInterval(refresh, 30_000);
}
