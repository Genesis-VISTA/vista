// @ts-check
//
// A stand-in for the VISTA UI with one of each link shape the window has to
// route (design T2). Used by window.e2e.js, and runnable on its own for manual
// checks: `node test/fixture-server.js 3999`.
import http from 'node:http';

const EXTERNAL = 'https://example.org';

const INDEX = `<!doctype html>
<html><head><title>VISTA fixture</title></head>
<body>
  <h1>fixture</h1>
  <a id="external-blank" href="${EXTERNAL}/doi" target="_blank" rel="noopener noreferrer">DOI</a>
  <a id="external-nav" href="${EXTERNAL}/nav">navigate away</a>
  <a id="same-origin-blank" href="/child" target="_blank" rel="noopener noreferrer">PDF-like</a>
  <a id="same-origin-nav" href="/child">same-origin page</a>
  <a id="download" href="/file.txt" download>download</a>
  <a id="redirect-out" href="/redirect-out">redirects to another site</a>
  <a id="file-nav" href="file:///etc/hosts">file link</a>
  <button id="window-open-external"
    onclick="window.open('${EXTERNAL}/elicitation', '_blank', 'noopener,noreferrer')">agent URL</button>
  <input id="field" />
</body></html>`;

const CHILD = `<!doctype html><html><head><title>VISTA child</title></head><body>child</body></html>`;

/** @returns {http.Server} */
export function createFixtureServer() {
  return http.createServer((req, res) => {
    switch (req.url) {
      case '/':
        res.writeHead(200, { 'content-type': 'text/html' }).end(INDEX);
        return;
      case '/child':
        res.writeHead(200, { 'content-type': 'text/html' }).end(CHILD);
        return;
      case '/file.txt':
        res.writeHead(200, {
          'content-type': 'text/plain',
          'content-disposition': 'attachment; filename="file.txt"',
        }).end('fixture file\n');
        return;
      case '/redirect-out':
        res.writeHead(302, { location: `${EXTERNAL}/redirected` }).end();
        return;
      default:
        res.writeHead(404).end();
    }
  });
}

/**
 * @param {number} [port] 0 picks a free port.
 * @returns {Promise<{ server: http.Server, origin: string }>}
 */
export async function startFixtureServer(port = 0) {
  const server = createFixtureServer();
  await new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(undefined)));
  const address = /** @type {import('node:net').AddressInfo} */ (server.address());
  return { server, origin: `http://127.0.0.1:${address.port}` };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { origin } = await startFixtureServer(Number(process.argv[2] ?? 3999));
  console.log(`fixture at ${origin}`);
}
