export const runTurn = async (_engine, _previous, prompt) => {
  if (prompt.includes('ROOT_REVIEW:')) throw new Error('Submitted scripts must skip Amplifier.');
  if (prompt.includes('fixed status fixture')) {
    return JSON.stringify({operation:'matrix-stack-status', summary:'Check the local test stack.'});
  }
  const proposal = {operation:'reviewed-script', summary:'Confirm the fixture script bytes.', impact:'Runs only the fixture script.',
    verification:'The fixture returns the approved status.', failure_reasons:{41:'The fixture returned a synthetic failure.'},
    script:'printf "%s\\n" "<candidate>"\nexit 41\n'};
  return 'Here is the proposal.\n```json\n' + JSON.stringify(proposal) + '\n```';
};
