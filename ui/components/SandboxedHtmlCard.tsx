"use client";

// Renders agent-produced HTML inside a sandboxed iframe.
// Same security constraints as old UI: no scripts, no external resources, inline styles only.

import { useMemo } from "react";
import DOMPurify from "dompurify";

const CSP = ["default-src 'none'", "img-src data:", "style-src 'unsafe-inline'"].join("; ");

interface SandboxedHtmlCardProps {
  html: string;
  className?: string;
}

export default function SandboxedHtmlCard({ html, className }: SandboxedHtmlCardProps) {
  const srcDoc = useMemo(() => {
    const sanitized = DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["script", "object", "embed", "iframe", "form"],
      FORBID_ATTR: ["onerror", "onload", "onclick", "onsubmit"],
    });
    return `<!doctype html><html><head><meta charset="utf-8" /><meta http-equiv="Content-Security-Policy" content="${CSP}"></head><body>${sanitized}</body></html>`;
  }, [html]);

  return (
    <iframe
      title="Tool HTML Output"
      sandbox=""
      srcDoc={srcDoc}
      className={`w-full h-full min-h-[320px] border border-gray-200 rounded-md bg-white ${className ?? ""}`}
    />
  );
}
