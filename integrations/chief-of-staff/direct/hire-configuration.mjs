import { createHash } from 'node:crypto';

const modes = { chatgpt_codex: 'ChatGPT Codex', chatgpt_plan: 'ChatGPT Plan', openai_api: 'OpenAI API', anthropic_api: 'Anthropic API' };
const voteStages = ['mode', 'account', 'model', 'effort', 'tools'];
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const escapeMarkdown = value => String(value ?? '').replace(/[\\`*_{}\[\]<>#+|]/gu, '\\$&');

function requestMarkdown(summary) {
  const matrixLinks = /(https:\/\/matrix\.to\/#\/[a-zA-Z0-9!$._%:/-]+)/gu;
  return String(summary ?? '').split(matrixLinks).map((part, index) =>
    index % 2 ? '<' + part + '>' : escapeMarkdown(part)).join('');
}

function requiredWeb(operation) {
  return operation.web === true || operation.profile.profile.capabilities.some(capability =>
    /(?:^|[-_])(?:web|browsing)(?:$|[-_])/iu.test(capability.code));
}

export function hiringMarkdown(operation, summary) {
  const { profile, provider, tools, firstTask } = operation;
  const { persona, limits, contacts, capabilities } = profile.profile;
  return [
    '# ' + escapeMarkdown(profile.name) + ' (' + escapeMarkdown(profile.key) + ')',
    '', '## Persona and duties', escapeMarkdown(persona?.purpose ?? limits),
    ...(persona?.duties ?? []).map(duty => '- ' + escapeMarkdown(duty)),
    '', 'Working style · ' + escapeMarkdown(persona?.working_style ?? limits),
    'Limits · ' + escapeMarkdown(limits), 'Office contacts · ' + (contacts ?? ['task-manager']).map(escapeMarkdown).join(', '),
    '', '## Capabilities',
    ...capabilities.map(item => '- ' + escapeMarkdown(item.code) + ' · ' +
      escapeMarkdown(item.description) + ' (' + item.resources.map(escapeMarkdown).join(', ') + ')'),
    '', '## Amplifier connection',
    '- Mode · ' + modes[provider.mode], '- Account · ' + escapeMarkdown(provider.label),
    '- Model · ' + escapeMarkdown(profile.model), '- Reasoning · ' + escapeMarkdown(profile.effort),
    '', '## Tools',
    ...(tools.length ? tools.map(tool => '- ' + escapeMarkdown(tool.id) + ' (' + escapeMarkdown(tool.version) +
      ') · ' + escapeMarkdown(tool.permissions) + '. Methods · ' + (tool.methods ?? []).map(escapeMarkdown).join(', ')) : ['No tools requested.']),
    '', '## First task and acceptance', escapeMarkdown(firstTask),
    '', '## Request', requestMarkdown(summary), '',
  ].join('\n');
}

function thread(request, parent = request) {
  return { rel_type: 'm.thread', event_id: request, is_falling_back: true, 'm.in_reply_to': { event_id: parent } };
}

export function createHireConfiguration({ db, access, send }) {
  db.exec(`CREATE TABLE IF NOT EXISTS hiring_config (
    request TEXT PRIMARY KEY, state TEXT NOT NULL, prompt TEXT UNIQUE, signature TEXT, last_choice TEXT
  );
  CREATE TABLE IF NOT EXISTS hiring_reconnect (
    request TEXT PRIMARY KEY, login TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0
  )`);
  const read = db.prepare('SELECT * FROM hiring_config WHERE request=?');
  const find = db.prepare('SELECT * FROM hiring_config WHERE prompt=?');
  const write = db.prepare('UPDATE hiring_config SET state=?,prompt=NULL,signature=NULL,last_choice=? WHERE request=?');
  const save = (request, state, choice = null) => write.run(JSON.stringify(state), choice, request);

  async function show(request, row, state, title, options = [], extra = '') {
    const optionsText = options.map(({ label }, index) => `${index + 1}. ${label}`).join('\n');
    const instruction = options.length ? '\n\nReply to this message with !hire followed by the option number, for example !hire 1.' : '';
    const body = [title, optionsText, extra].filter(Boolean).join('\n\n') + instruction;
    const signature = hash([request, state, body, options]);
    if (row.signature === signature) return;
    const prompt = await send(signature, { msgtype: 'm.text', body, 'm.mentions': { user_ids: [] },
      'm.relates_to': thread(request) });
    db.prepare('UPDATE hiring_config SET state=?,prompt=?,signature=? WHERE request=?')
      .run(JSON.stringify({ ...state, options }), prompt, signature, request);
  }

  async function prepare(row, template) {
    const { request } = row;
    db.prepare('INSERT OR IGNORE INTO hiring_config (request,state) VALUES (?,?)').run(request, '{"stage":"mode"}');
    const saved = read.get(request);
    const { options, ...state } = JSON.parse(saved.state);
    const { stage, mode, account, model, effort, login } = state;
    const title = 'Configure ' + template.agent;
    if (stage === 'mode') {
      await show(request, saved, state, title + ' · choose a provider connection',
        Object.entries(modes).map(([value, label]) => ({ value, label })));
    } else if (stage === 'account') {
      const { accounts = [] } = await access({ action: 'accounts' });
      await show(request, saved, state, title + ' · choose an account',
        [...accounts.filter(item => item.mode === mode).map(item =>
          ({ value: item, label: item.label + (item.ready ? '' : ' · reconnect') })),
        ...(!mode.endsWith('_api') ? [{ value: 'connect', label: 'Connect a ' + modes[mode] + ' account' }] : [])],
        mode.endsWith('_api') ? 'Connect API keys from the VPS terminal with the provider setup command.' : '');
    } else if (stage === 'login') {
      const status = await access({ action: 'login-status', id: login });
      if (status.status === 'ready') {
        save(request, { ...state, account: status.account, stage: 'model', login: undefined });
      } else {
        const lines = [status.url, status.code && 'Enter this one-time code on OpenAI’s page: ' + status.code].filter(Boolean);
        await show(request, saved, state, title + ' · ' + (lines.length ? 'authorize in your browser' :
          status.status === 'pending' ? 'waiting for the sign-in link' : 'sign-in stopped'),
        [{ value: 'back', label: 'Return to account selection' }], lines.join('\n'));
      }
    } else if (stage === 'model') {
      const { models = [] } = await access({ action: 'catalog', account: account.id });
      const list = models.filter(item => item.efforts?.length);
      await show(request, saved, state, title + (list.length ? ' · choose the model' : ' · the selected account has no available models'),
        list.length ? list.map(item => ({ value: item, label: item.id })) :
          [{ value: 'back', label: 'Return to account selection' }]);
    } else if (stage === 'effort') {
      await show(request, saved, state, title + ' · choose reasoning effort',
        model.efforts.map(value => ({ value, label: value })));
    } else if (stage === 'tools') {
      const { tools = [] } = (requiredWeb(template) || template.chooseTools) ? await access({ action: 'tools' }) : {};
      const requireWeb = requiredWeb(template);
      const selected = state.tools ?? [];
      const remaining = tools.filter(tool => !selected.some(item => item.id === tool.id));
      const webMissing = requireWeb && !selected.some(tool => tool.purpose === 'public-web');
      const choices = remaining.filter(tool => !webMissing || tool.purpose === 'public-web');
      if (webMissing && !choices.length) {
        await show(request, saved, state, title + ' · public web access required',
          [], 'Add a verified public web tool to the deployment’s hiring catalog. This request will resume when one becomes available.');
        return null;
      }
      if (!selected.length && webMissing && tools.length === 1) {
        save(request, { ...state, stage: 'complete', tools: choices });
      } else if (choices.length) {
        await show(request, saved, state, title + ' · choose tool access', [
          ...(!webMissing ? [{ value: 'finish', label: 'Continue with ' + (selected.length ? 'the selected tools' : 'no tools') }] : []),
          ...choices.map(value => ({ value, label: 'Add ' + value.id + ' · ' + value.permissions })),
        ]);
      } else {
        save(request, { ...state, stage: 'complete', tools: selected });
      }
    } else if (stage === 'complete') {
      return {
        kind: 'add', agent: template.agent,
        profile: { ...template.profile, model: model.id, effort },
        provider: { mode, account: account.id, label: account.label },
        tools: state.tools,
        ...(template.resume && {resume: true}),
        firstTask: template.firstTask || 'Complete a representative task in Office and return the result with evidence in its original thread.',
      };
    }
    return null;
  }

  async function accept(event, raw) {
    const content = event.content;
    if (event.type !== 'm.room.message' || content?.msgtype !== 'm.text' || typeof content.body !== 'string' ||
        content['m.relates_to']?.rel_type === 'm.replace') return null;
    const text = content.body.replace(/^(?:>[^\n]*\n)+\n/u, '').trim();
    const reconnect = /^!hire reconnect ([a-f0-9]{24})$/u.exec(text);
    if (reconnect) {
      const result = await access({ action: 'accounts' });
      const account = result.accounts?.find(item => item.id === reconnect[1]);
      if (!account) return 'That ChatGPT connection is unavailable.';
      const begin = await access({ action: 'begin', mode: account.mode, account: account.id, event: raw.event_id });
      if (!begin.id) return 'Sign-in could not start. Check the private hiring route before retrying.';
      db.prepare('INSERT OR IGNORE INTO hiring_reconnect (request,login) VALUES (?,?)').run(raw.event_id, begin.id);
      return '';
    }
    const selection = /^!hire ([1-9]\d{0,3})$/u.exec(text);
    if (!selection) return null;
    const parent = content['m.relates_to']?.['m.in_reply_to']?.event_id;
    const saved = parent && find.get(parent);
    if (!saved) return 'Reply to the current hiring setup message to choose an option.';
    const { stage, options, ...before } = JSON.parse(saved.state);
    const option = options?.[Number(selection[1]) - 1];
    if (!option) return 'Choose a number from the options in that message.';
    const { value } = option;
    let next;
    if (stage === 'mode') next = { stage: 'account', mode: value };
    else if (stage === 'account') {
      if (value === 'connect' || !value.ready) {
        const result = await access({ action: 'begin', mode: before.mode, account: value.id, event: raw.event_id });
        if (!result.id) return 'The connection could not start. Reply to the same message to retry.';
        next = { ...before, stage: 'login', login: result.id };
      } else next = { ...before, account: value, stage: 'model' };
    } else if (stage === 'login' || stage === 'model' && value === 'back') {
      next = { stage: 'account', mode: before.mode };
    } else if (stage === 'model') next = { ...before, model: value, stage: 'effort' };
    else if (stage === 'effort') next = { ...before, effort: value, stage: 'tools' };
    else if (stage === 'tools') next = value === 'finish' ? { ...before, stage: 'complete', tools: before.tools ?? [] }
      : { ...before, tools: [...(before.tools ?? []), value] };
    if (next && (voteStages.includes(stage) || stage === 'login')) save(saved.request, next, raw.event_id);
    return '';
  }

  async function reconnectTick() {
    for (const row of db.prepare('SELECT request,login FROM hiring_reconnect WHERE done=0 LIMIT 8').all()) {
      const status = await access({ action: 'login-status', id: row.login });
      if (status.status === 'pending' && !status.url) continue;
      const body = status.status === 'ready' ? 'Your ChatGPT connection is ready. Approved hires will resume automatically.'
        : status.status === 'pending' ? ['Authorize the ChatGPT connection in your browser.', status.url,
          status.code && 'Enter this one-time code on OpenAI’s page: ' + status.code].filter(Boolean).join('\n')
          : 'Sign-in did not finish. Send a fresh reconnect request when you are ready.';
      await send(hash([row.request, status.status, body]), { msgtype: 'm.text', body, 'm.mentions': {user_ids: []},
        'm.relates_to': thread(row.request) });
      if (status.status !== 'pending') db.prepare('UPDATE hiring_reconnect SET done=1 WHERE request=?').run(row.request);
    }
  }

  return { prepare, accept, reconnectTick, pending: agent => access({ action: 'pending', agent }) };
}
