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
  <a id="same-origin-blank" href="/child" target="_blank" rel="noopener noreferrer">child page</a>
  <a id="pdf-blank" href="/paper.pdf" target="_blank" rel="noreferrer">Open PDF</a>
  <a id="same-origin-nav" href="/child">same-origin page</a>
  <a id="download" href="/file.txt" download>download</a>
  <a id="redirect-out" href="/redirect-out">redirects to another site</a>
  <a id="file-nav" href="file:///etc/hosts">file link</a>
  <button id="window-open-external"
    onclick="window.open('${EXTERNAL}/elicitation', '_blank', 'noopener,noreferrer')">agent URL</button>
  <input id="field" />
</body></html>`;

const CHILD = `<!doctype html><html><head><title>VISTA child</title></head><body>child</body></html>`;

// A one-page PDF, served inline the way the knowledge-base publication route
// serves papers, so the test exercises Electron's built-in viewer.
const PDF = pdfWithText('fixture paper');

/** @param {string} text */
function pdfWithText(text) {
  const stream = `BT /F1 24 Tf 72 720 Td (${text}) Tj ET`;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let body = '%PDF-1.4\n';
  const offsets = objects.map((object, i) => {
    const offset = body.length;
    body += `${i + 1} 0 obj\n${object}\nendobj\n`;
    return offset;
  });
  const xref = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('');
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, 'latin1');
}

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
      case '/paper.pdf':
        res.writeHead(200, {
          'content-type': 'application/pdf',
          'content-disposition': 'inline; filename="paper.pdf"',
        }).end(PDF);
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
