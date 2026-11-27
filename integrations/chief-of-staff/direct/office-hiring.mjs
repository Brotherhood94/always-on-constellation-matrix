import { canDispatch } from '../../../agents/connector/agent-catalog.mjs';
import { staffingNotice, staffingOutcome } from '../../../agents/connector/staffing-office.mjs';

const terminal = ['ready', 'declined', 'expired', 'rejected', 'conflict', 'unavailable'];

export function officeHireStatus(approval, agent, joined, needsWeb) {
  if (approval?.announced === 'provisioned') {
    if (!agent || !agent.active) return 'provider_required';
    if (!canDispatch(agent, joined) || !['Available', 'Running'].includes(agent.status)) return 'awaiting_readiness';
    if (needsWeb && !Object.values(agent.grants ?? {}).some(tool => tool.kind === 'mcp' && tool.purpose === 'public-web')) {
      return 'awaiting_web';
    }
    return 'ready';
  }
  if (['declined', 'expired', 'rejected'].includes(approval?.announced)) return approval.announced;
  if (approval?.action === 'approve') return 'provisioning';
  return approval?.poll ? 'awaiting_approval' : 'preparing';
}

export function createOfficeHiring({ db, client, office, owner, planner, catalog, enabled, approvals, say, draft,
  allowedKey, publish, report, now = Date.now }) {
  db.exec(`CREATE TABLE IF NOT EXISTS office_hires (
    request TEXT PRIMARY KEY, thread TEXT NOT NULL, handoff TEXT NOT NULL, manager TEXT NOT NULL, requester TEXT NOT NULL,
    agent TEXT NOT NULL, brief TEXT NOT NULL, web INTEGER NOT NULL, profile TEXT, private_source TEXT,
    status TEXT, checked INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0
  )`);
  const insert = db.prepare(`INSERT OR IGNORE INTO office_hires (request,thread,handoff,manager,requester,agent,brief,web)
    VALUES (?,?,?,?,?,?,?,?)`);
  const poll = db.prepare('SELECT poll,action,announced FROM roster_requests WHERE request=?');
  const pending = db.prepare(`SELECT 1 FROM roster_requests WHERE done=0 AND json_extract(operation,'$.agent')=?
    AND json_extract(operation,'$.kind')='add' AND request<>COALESCE(?, '')`);
  const prior = db.prepare('SELECT 1 FROM office_hires WHERE agent=? AND request<>? AND private_source IS NOT NULL AND done=0');

  function queue(event, { operation, requester }) {
    if (![owner, planner].includes(requester)) return;
    insert.run(event.request, event.thread, event.id, event.delegator, requester, operation.key, operation.brief, Number(operation.web));
  }

  async function tick() {
    const row = db.prepare('SELECT * FROM office_hires WHERE done=0 AND checked<=? ORDER BY checked,rowid LIMIT 1').get(now());
    if (!row) return;
    db.prepare('UPDATE office_hires SET checked=? WHERE request=?').run(now() + 3000, row.request);
    const agents = catalog.fresh() ? catalog.current().agents : null;
    const agent = agents?.find(item => item.key === row.agent);
    const approval = row.private_source && poll.get(row.private_source);
    let status;
    if (!enabled) status = 'unavailable';
    else if (!row.private_source && (!allowedKey(row.agent) || row.agent === owner.slice(1).split(':', 1)[0] || agent || pending.get(row.agent, null) || prior.get(row.agent, row.request))) {
      status = 'conflict';
    } else if (!agents && approval?.announced === 'provisioned') return;
    else {
      const joined = approval?.announced === 'provisioned' ? new Set(await client.getJoinedRoomMembers(office)) : new Set();
      status = officeHireStatus(approval, agent, joined, row.web);
    }
    if (status !== row.status) {
      const receipt = { request: row.request, handoff: row.handoff, agent: row.agent, status };
      const recipient = { id: row.requester, name: row.requester === planner ? 'Dot' : owner.slice(1).split(':', 1)[0] };
      const sent = await publish(row.request, status, staffingNotice(receipt, row.thread, recipient));
      await report?.(row, sent, staffingOutcome(receipt, sent));
      db.prepare(`UPDATE office_hires SET status=?,done=?,brief=CASE WHEN ? THEN '' ELSE brief END
        WHERE request=?`).run(status, Number(terminal.includes(status)), Number(terminal.includes(status)), row.request);
      return;
    }
    if (status !== 'preparing' || !agents || row.private_source) return;
    if (!row.profile) {
      const profile = await draft(row.agent, row.brief);
      row.profile = JSON.stringify(profile);
      db.prepare('UPDATE office_hires SET profile=? WHERE request=?').run(row.profile, row.request);
    }
    const link = `https://matrix.to/#/${encodeURIComponent(office)}/${encodeURIComponent(row.request)}`;
    const summary = `Requested in Office: ${link}\n\n${row.brief}` +
      (row.web ? '\n\nPublic web access needs a separate verified tool grant.' : '');
    const source = await say('hire-office:' + row.request, 'Review the hiring proposal for ' + row.agent + '.\n\n' + summary);
    db.exec('BEGIN IMMEDIATE');
    try {
      approvals.enqueue(source, { kind: 'add', agent: row.agent, profile: JSON.parse(row.profile) }, summary, 'agent');
      db.prepare("UPDATE office_hires SET private_source=?,brief='',profile=NULL WHERE request=?").run(source, row.request);
      db.exec('COMMIT');
    } catch (error) {
      db.exec('ROLLBACK');
      throw error;
    }
  }
  return { queue, tick };
}
