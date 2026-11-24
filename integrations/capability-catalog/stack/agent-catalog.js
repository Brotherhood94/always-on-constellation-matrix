(() => {
  const section = document.querySelector('#agents')?.closest('section');
  if (!section) return;
  const details = section.appendChild(document.createElement('details'));
  details.appendChild(document.createElement('summary')).textContent = 'Agent roles, capabilities, and limits';
  const content = details.appendChild(document.createElement('div'));
  const status = content.appendChild(document.createElement('p'));
  status.setAttribute('role', 'status');
  const list = content.appendChild(document.createElement('div'));

  async function refresh() {
    try {
      const response = await fetch('./agents.json', { cache: 'no-store' });
      if (!response.ok) throw new Error('The catalog is unavailable');
      const catalog = await response.json();
      status.textContent = Date.now() - catalog.time * 1000 > 180000 ? 'Capability information is stale.' : '';
      const profiles = catalog.agents.map(agent => {
        const profile = document.createElement('article');
        profile.appendChild(document.createElement('h3')).textContent = `${agent.name} (${agent.role})`;
        profile.appendChild(document.createElement('p')).textContent = [agent.server, agent.model, agent.effort,
          agent.status === 'Registered' ? 'Registered · availability unverified' : agent.status].filter(Boolean).join(' · ');
        const capabilities = profile.appendChild(document.createElement('ul'));
        for (const capability of agent.capabilities) {
          capabilities.appendChild(document.createElement('li')).textContent =
            `${capability.description} (${capability.resources.join(', ')})`;
        }
        profile.appendChild(document.createElement('p')).textContent = agent.limits;
        return profile;
      });
      list.replaceChildren(...profiles);
    } catch {
      status.textContent = 'Capability information unavailable.';
    }
  }
  void refresh();
  setInterval(refresh, 60000);
})();
