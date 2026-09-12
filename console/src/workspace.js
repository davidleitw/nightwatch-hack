import {setupConnection} from './connect.js';

const recording = new URLSearchParams(location.search).get('source') === 'recording';
if (recording) {
  await import('./app.js');
} else {
  const mock = await setupConnection('live');
  if (mock) await import('./app.js');
  else {
    const {startWorkspace} = await import('./investigations.js');
    startWorkspace();
  }
}
