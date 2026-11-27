export const runTurn = async (_engine, _previous, prompt) => {
  if (prompt.includes('ROOT_REVIEW:')) throw new Error('Submitted scripts must skip Amplifier.');
  if (prompt.includes('maintenance dashboard fixture')) {
    if (_engine.allowTools) {
      if (!_engine.workspace.includes('/workspace/constellation-matrix/')) throw new Error('Missing draft workspace.');
      return 'Update the dashboard fixture.';
    }
    return JSON.stringify({operation:'prepare-repository-change'});
  }
  if (prompt.includes('fixed status fixture')) {
    return JSON.stringify({operation:'matrix-stack-status', summary:'Check the local test stack.'});
  }
  const proposal = {operation:'reviewed-plan', summary:'Confirm the fixture script bytes.', impact:'Runs only the fixture script.',
    verification:'The fixture returns the approved status.', recovery:'Inspect the fixture status.',
    plan:{kind:'shell', verify:'test -d /tmp', script:'printf "%s\\n" "<candidate>"\nexit 41\n'}};
  return 'Here is the proposal.\n```json\n' + JSON.stringify(proposal) + '\n```';
};
