const keyPattern = /^[a-z0-9][a-z0-9_-]{0,63}$/u;
const ns = 'http://www.w3.org/2000/svg';
const halfWidth = 90;
const halfHeight = 29;

const healthTones = {
  available: 'good', running: 'good', healthy: 'good', ready: 'good', active: 'good',
  'sign-in required': 'warn', 'setup required': 'warn', starting: 'warn', registered: 'warn', pending: 'warn',
  unavailable: 'down', exited: 'down', stopped: 'down', unhealthy: 'down', failed: 'down',
};

export function statusTone(status) {
  const key = String(status).toLowerCase();
  return Object.hasOwn(healthTones, key) ? healthTones[key] : 'neutral';
}

export function connectionRoute(hash) {
  if (hash === '#/connections') return {};
  const matched = /^#\/connections\/([a-z0-9_-]{1,64})\/([a-z0-9_-]{1,64})$/u.exec(hash);
  if (matched && keyPattern.test(matched[1]) && keyPattern.test(matched[2])) {
    return { source: matched[1], target: matched[2] };
  }
  return null;
}

export function connectionGraph(catalog) {
  const nodes = (catalog?.agents ?? []).filter(agent =>
    typeof agent.key === 'string' && keyPattern.test(agent.key) && agent.role !== 'observer');
  const available = catalog?.contactPolicy === true && catalog.dynamicHandoffs === true;
  const keys = new Set(nodes.map(({key}) => key));
  const edges = available ? nodes.flatMap(({key, contacts}) =>
    [...new Set(contacts ?? [])].filter(target => target !== key && keys.has(target)).map(target => [key, target])) : [];
  return { nodes, edges, available };
}

export function connectionDetails(report, route) {
  const changes = report?.changes ?? [];
  const handoffs = report?.handoffs ?? [];
  if (!route.source) return { changes, handoffs: [] };
  const matches = ([source, target]) => source === route.source && target === route.target;
  const localpart = id => typeof id === 'string' && /^@([^:]+):/u.exec(id)?.[1];
  return {
    changes: changes.filter(change => [...change.added, ...change.removed].some(matches)),
    handoffs: handoffs.filter(row => matches([localpart(row.sender), localpart(row.recipient)])),
  };
}

const detailsHref = (source, target) => '#/connections/' + source + '/' + target;
const label = value => value.length > 19 ? value.slice(0, 18) + '…' : value;

function html(tag, value) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  return element;
}

function link(href, title) {
  const node = html('a', title);
  node.href = href;
  return node;
}

function svg(tag, attributes, content) {
  const element = document.createElementNS(ns, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (content !== undefined) element.textContent = content;
  return element;
}

function layout(nodes) {
  const center = nodes.find(agent => agent.role === 'coordinator') ?? nodes[0];
  const perimeter = nodes.filter(agent => agent !== center);
  const width = Math.max(860, perimeter.length * 92);
  const height = Math.max(perimeter.length > 2 ? 420 : 296, perimeter.length * 64);
  const middle = [width / 2, height / 2];
  const points = new Map([[center.key, middle]]);
  for (const [index, node] of perimeter.entries()) {
    const angle = 2 * Math.PI * index / perimeter.length - Math.PI / 4;
    points.set(node.key, [middle[0] + Math.cos(angle) * (middle[0] - 111),
      middle[1] + Math.sin(angle) * (middle[1] - 53)]);
  }
  return { points, width, height };
}

function edgeGeometry(start, end, paired) {
  const dx = end[0] - start[0], dy = end[1] - start[1];
  const distance = Math.hypot(dx, dy);
  const bend = paired ? Math.min(66, distance / 4) : 0;
  const control = [(start[0] + end[0]) / 2 - dy / distance * bend,
    (start[1] + end[1]) / 2 + dx / distance * bend];
  const bound = (point, towards) => {
    const x = towards[0] - point[0], y = towards[1] - point[1];
    const ratio = 1 / Math.max(Math.abs(x) / (halfWidth + 5), Math.abs(y) / (halfHeight + 5));
    return [point[0] + x * ratio, point[1] + y * ratio];
  };
  return 'M ' + bound(start, control).join(' ') + ' Q ' + control.join(' ') + ' ' + bound(end, control).join(' ');
}

function graphDiagram(nodes, edges) {
  const { points, width, height } = layout(nodes);
  const names = new Map(nodes.map(agent => [agent.key, agent.name]));
  const edgeKeys = new Set(edges.map(edge => edge.join('/')));
  const diagram = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'group', 'aria-label': 'Directed Office handoffs' });
  diagram.style.minWidth = Math.max(660, width * 0.8) + 'px';
  const arrow = svg('marker', { id: 'office-arrow', markerWidth: 10, markerHeight: 10, refX: 9, refY: 5,
    orient: 'auto', markerUnits: 'userSpaceOnUse' });
  arrow.append(svg('path', { d: 'M 0 0 L 10 5 L 0 10 L 2.5 5 Z', class: 'graph-arrow' }));
  const definitions = svg('defs', {});
  definitions.append(arrow);
  diagram.append(definitions);
  for (const [source, target] of edges) {
    const name = `${names.get(source)} can assign to ${names.get(target)}`;
    const a = svg('a', { href: detailsHref(source, target), 'aria-label': name, class: 'graph-edge',
      'data-source': source, 'data-target': target });
    const d = edgeGeometry(points.get(source), points.get(target), edgeKeys.has(target + '/' + source));
    a.append(svg('title', {}, name), svg('path', { d, class: 'graph-edge-hit' }),
      svg('path', { d, class: 'graph-edge-line', 'marker-end': 'url(#office-arrow)' }));
    diagram.append(a);
  }
  for (const node of nodes) {
    const [x, y] = points.get(node.key);
    const status = node.status || 'Unavailable';
    const initials = node.name.trim().split(/\s+/u).slice(0, 2).map(word => [...word][0]).join('').toUpperCase();
    const start = x - halfWidth;
    const a = svg('a', { href: '#/agents/' + node.key, class: 'graph-node',
      'aria-label': `${node.name} persona and duties, ${status}`, 'data-agent': node.key, 'data-tone': statusTone(status) });
    a.toggleAttribute('data-coordinator', node.role === 'coordinator');
    a.append(svg('title', {}, node.name + ' · ' + status),
      svg('rect', { x: start, y: y - halfHeight, width: halfWidth * 2, height: halfHeight * 2, rx: 12, class: 'node-frame' }),
      svg('rect', { x: start + 11, y: y - 16, width: 33, height: 33, rx: 9, class: 'node-icon' }),
      svg('text', { x: start + 27.5, y: y + 5, 'text-anchor': 'middle', class: 'node-letter' }, initials),
      svg('text', { x: start + 53, y: y - 3 }, label(node.name)),
      svg('circle', { cx: start + 56, cy: y + 13, r: 3.2, class: 'node-indicator' }),
      svg('text', { x: start + 64, y: y + 17, class: 'node-status' }, label(status)));
    diagram.append(a);
  }
  return diagram;
}

let graphSignature;
export function renderConnections(catalog) {
  const { nodes, edges, available } = connectionGraph(catalog);
  const signature = JSON.stringify([available, nodes.map(({key, name, status, role}) => [key, name, status, role]), edges]);
  if (signature === graphSignature) return;
  graphSignature = signature;
  const frame = document.getElementById('connection-graph');
  const board = document.getElementById('connection-board');
  const list = document.getElementById('connection-list');
  const message = document.getElementById('connection-state');
  board.hidden = !available || !nodes.length;
  list.hidden = board.hidden;
  message.textContent = !catalog ? 'Agent catalog unavailable.' : !available ?
    'Directed contacts are unavailable for this roster.' : nodes.length ?
      'Arrows show permitted handoff directions. Open an arrow to see its history.' :
      'No Office agents registered.';
  frame.replaceChildren();
  if (board.hidden) return;
  const diagram = graphDiagram(nodes, edges);
  frame.append(diagram);
  const picker = document.getElementById('connection-focus');
  const previous = picker.value;
  picker.replaceChildren(new Option('All agents', ''), ...nodes.map(node => new Option(node.name, node.key)));
  picker.value = nodes.some(node => node.key === previous) ? previous : '';
  const applyFocus = () => {
    const selected = picker.value;
    let outgoing = 0;
    let incoming = 0;
    for (const line of diagram.querySelectorAll('.graph-edge')) {
      if (!selected) delete line.dataset.focus;
      else if (line.dataset.source === selected) { line.dataset.focus = 'outgoing'; outgoing++; }
      else if (line.dataset.target === selected) { line.dataset.focus = 'incoming'; incoming++; }
      else line.dataset.focus = 'muted';
    }
    for (const node of diagram.querySelectorAll('.graph-node')) node.toggleAttribute('data-active', node.dataset.agent === selected);
    document.getElementById('connection-count').textContent = selected ?
      `${outgoing} outgoing · ${incoming} incoming (dashed)` :
      `${nodes.length} ${nodes.length === 1 ? 'agent' : 'agents'} · ${edges.length} ${edges.length === 1 ? 'route' : 'routes'}`;
  };
  picker.onchange = () => {
    applyFocus();
    diagram.querySelector('.graph-node[data-active]')?.scrollIntoView({ block: 'nearest', inline: 'center' });
  };
  applyFocus();
  const rows = html('ul');
  for (const node of nodes) {
    const row = html('li');
    row.append(link('#/agents/' + node.key, node.name), document.createTextNode(' · ' + (node.status || 'Unavailable')));
    const peers = html('ul');
    for (const [, target] of edges.filter(([source]) => source === node.key)) {
      const peer = html('li');
      peer.append(link(detailsHref(node.key, target), 'Can assign to ' + nodes.find(item => item.key === target).name));
      peers.append(peer);
    }
    if (peers.childElementCount) row.append(peers);
    rows.append(row);
  }
  document.getElementById('connection-list-rows').replaceChildren(rows);
}

function timestamp(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? date.toLocaleString() : 'Time unavailable';
}

function matrixLink(room, event) {
  if (typeof room !== 'string' || typeof event !== 'string' ||
      !/^![^\s/#?%]+$/u.test(room) || !/^\$[^\s/#?%]+$/u.test(event)) return null;
  return 'https://matrix.to/#/' + encodeURIComponent(room) + '/' + encodeURIComponent(event);
}

function changeRow(change, route, room) {
  const row = html('li');
  row.append(html('strong', `Revision ${change.sequence} · ${timestamp(change.at)}`),
    html('p', `${change.actor} · ${change.reason}`));
  const details = html('ul');
  const add = text => details.append(html('li', text));
  const pairName = key => change.names?.[key] ?? key;
  for (const [items, verb] of [[change.added, 'Added'], [change.removed, 'Removed']]) {
    for (const [source, target] of items) {
      if (!route.source || (source === route.source && target === route.target)) {
        const action = change.kind === 'baseline' ? 'Initially allowed' : verb;
        const direction = `${pairName(source)} → ${pairName(target)}`;
        if (route.source) add(action + ' · ' + direction);
        else {
          const item = html('li', action + ' · ');
          item.append(link(detailsHref(source, target), direction));
          details.append(item);
        }
      }
    }
  }
  if (!route.source && change.kind !== 'baseline') {
    for (const key of change.joined) add('Registered ' + pairName(key));
    for (const key of change.left) add('Removed ' + pairName(key));
    for (const [key, previous] of Object.entries(change.renamed)) add(`Renamed ${previous} to ${pairName(key)}`);
    if (change.enforced !== change.previous_enforced) add(change.enforced ? 'Directed contacts enabled' : 'Directed contacts disabled');
  }
  if (details.childElementCount) row.append(details);
  if (change.approval) row.append(html('p', 'Approved in Element · ' + change.approval.slice(0, 12)));
  const href = matrixLink(room, change.evidence);
  if (href) row.append(link(href, 'Open supporting Office evidence'));
  return row;
}

const handoffStates = { dispatched: 'Dispatched', accepted: 'Accepted', returned: 'Returned', blocked: 'Blocked' };
const outcomeStates = { verified: 'Verified', blocked: 'Blocked', in_progress: 'In progress' };

export function renderConnectionDetails(view, route, report, catalog, freshness) {
  const names = new Map();
  for (const change of [...(report?.changes ?? [])].reverse()) {
    for (const [key, name] of Object.entries(change.names)) names.set(key, name);
  }
  for (const agent of catalog?.agents ?? []) names.set(agent.key, agent.name);
  const name = key => names.get(key) ?? key;
  view.replaceChildren(link('#connections-heading', '← Team connections'), html('h2', route.source ?
    `${name(route.source)} → ${name(route.target)}` : 'Connection changes'));
  if (route.source) view.append(link('#/connections', 'View all connection changes'));
  if (report?.version !== 1) {
    view.append(html('p', 'Connection history is unavailable. The current graph uses the agent catalog.'));
    return;
  }
  view.append(html('p', freshness(report.time)));
  if (report.coverage) view.append(html('p', 'Permission history starts ' + timestamp(report.coverage) + '.'));
  if (report.pending || (catalog?.policyRevision && catalog.policyRevision !== report.changes[0]?.id)) {
    view.append(html('p', 'The history is catching up with the current agent catalog.'));
  }
  const { changes, handoffs } = connectionDetails(report, route);
  view.append(html('h3', 'Permission changes'));
  if (report.changesLimited) view.append(html('p', 'Showing the latest 100 revisions across Office.'));
  if (!changes.length) view.append(html('p', 'No recorded permission changes' + (route.source ? ' for this direction.' : '.')));
  else {
    const list = html('ol');
    for (const change of changes) list.append(changeRow(change, route, report.room));
    view.append(list);
  }
  if (!route.source) return;
  view.append(html('h3', 'Office handoffs · last 30 days'));
  if (report.unclassified) view.append(html('p', 'Older unclassified Office evidence is excluded from this list.'));
  if (report.handoffsLimited) view.append(html('p', 'Showing the latest 500 classified handoffs across Office.'));
  const list = html('ol');
  for (const row of handoffs) {
    const item = html('li');
    item.append(html('strong', handoffStates[row.status] ?? 'Dispatched'),
      html('p', 'Dispatched · ' + timestamp(row.at)));
    if (row.acceptedAt) item.append(html('p', 'Accepted · ' + timestamp(row.acceptedAt)));
    if (row.returnedAt) item.append(html('p', (row.status === 'blocked' ? 'Blocked · ' : 'Returned · ') + timestamp(row.returnedAt)));
    item.append(html('p', 'Task outcome · ' + (outcomeStates[row.outcome] ?? 'Unavailable')));
    const href = matrixLink(report.room, row.thread);
    if (href) item.append(link(href, 'Open Office thread'));
    if (row.revision) item.append(html('p', 'Policy revision · ' + row.revision));
    list.append(item);
  }
  if (list.childElementCount) view.append(list);
  else view.append(html('p', 'No classified handoffs for this direction in the last 30 days.'));
}
