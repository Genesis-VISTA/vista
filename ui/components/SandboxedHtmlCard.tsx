"use client";

import { useMemo } from "react";
import DOMPurify from "dompurify";

const CSP = [
  "default-src 'none'",
  "img-src data:",
  "style-src 'unsafe-inline'"
].join("; ");

export default function SandboxedHtmlCard({ html }: { html: string }) {
  const sanitized = useMemo(() => {
    return DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["script", "object", "embed", "iframe", "form"],
      FORBID_ATTR: ["onerror", "onload", "onclick", "onsubmit"]
    });
  }, [html]);

  const srcDoc = `<!doctype html><html><head><meta charset="utf-8" /><meta http-equiv="Content-Security-Policy" content="${CSP}"></head><body>${sanitized}</body></html>`;

  return (
    <iframe
      title="Tool HTML Output"
      sandbox=""
      srcDoc={srcDoc}
      style={{ width: "100%", minHeight: 360, border: "1px solid #e2d6c6", borderRadius: 12, background: "#fff" }}
    />
  );
}
