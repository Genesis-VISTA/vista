/**
 * @jest-environment node
 */
import path from "path";
import fs from "fs";
import os from "os";

let tmpUploadsDir: string;

jest.mock("@/app/config", () => {
  const _path = require("path");
  const _os = require("os");
  const _fs = require("fs");
  const dir = _fs.mkdtempSync(_path.join(_os.tmpdir(), "vista-uploads-test-"));
  return {
    config: {
      skillsDir: dir,
      uploadsDir: dir,
    },
    __testSetUploadsDir: (d: string) => {
      const mod = require("@/app/config");
      mod.config.uploadsDir = d;
    },
  };
});

beforeAll(() => {
  tmpUploadsDir = fs.mkdtempSync(path.join(os.tmpdir(), "vista-uploads-"));
});

afterAll(() => {
  fs.rmSync(tmpUploadsDir, { recursive: true, force: true });
});

beforeEach(() => {
  // Point config to our temp uploads dir
  const { __testSetUploadsDir } = require("@/app/config");
  __testSetUploadsDir(tmpUploadsDir);

  // Clean up any files from previous tests
  for (const file of fs.readdirSync(tmpUploadsDir)) {
    fs.unlinkSync(path.join(tmpUploadsDir, file));
  }
});

describe("GET /api/uploads", () => {
  it("returns empty array when no files exist", async () => {
    const { GET } = await import("@/app/api/uploads/route");
    const res = await GET();
    const data = await res.json();
    expect(Array.isArray(data)).toBe(true);
    expect(data).toHaveLength(0);
  });

  it("lists uploaded files with metadata", async () => {
    fs.writeFileSync(path.join(tmpUploadsDir, "test.csv"), "a,b,c\n1,2,3");

    const { GET } = await import("@/app/api/uploads/route");
    const res = await GET();
    const data = await res.json();
    expect(data).toHaveLength(1);
    expect(data[0].name).toBe("test.csv");
    expect(data[0].size).toBeGreaterThan(0);
    expect(data[0].modifiedAt).toBeTruthy();
  });
});

describe("POST /api/uploads", () => {
  it("uploads a file successfully", async () => {
    const { POST } = await import("@/app/api/uploads/route");

    const formData = new FormData();
    const blob = new Blob(["hello world"], { type: "text/plain" });
    formData.append("files", new File([blob], "hello.txt"));

    const req = new Request("http://localhost:3000/api/uploads", {
      method: "POST",
      body: formData,
    });

    const res = await POST(req);
    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.saved).toContain("hello.txt");

    // Verify file exists on disk
    expect(fs.existsSync(path.join(tmpUploadsDir, "hello.txt"))).toBe(true);
  });

  it("returns 400 when no files provided", async () => {
    const { POST } = await import("@/app/api/uploads/route");

    const formData = new FormData();
    const req = new Request("http://localhost:3000/api/uploads", {
      method: "POST",
      body: formData,
    });

    const res = await POST(req);
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.error).toContain("No files");
  });

  it("sanitizes filenames", async () => {
    const { POST } = await import("@/app/api/uploads/route");

    const formData = new FormData();
    const blob = new Blob(["data"], { type: "text/plain" });
    formData.append("files", new File([blob], "my file (1).txt"));

    const req = new Request("http://localhost:3000/api/uploads", {
      method: "POST",
      body: formData,
    });

    const res = await POST(req);
    const data = await res.json();
    expect(data.ok).toBe(true);
    // Special chars should be replaced with underscores
    expect(data.saved[0]).not.toContain(" ");
    expect(data.saved[0]).not.toContain("(");
  });

  it("handles duplicate filenames with suffix", async () => {
    // Create an existing file
    fs.writeFileSync(path.join(tmpUploadsDir, "dup.txt"), "original");

    const { POST } = await import("@/app/api/uploads/route");

    const formData = new FormData();
    const blob = new Blob(["new content"], { type: "text/plain" });
    formData.append("files", new File([blob], "dup.txt"));

    const req = new Request("http://localhost:3000/api/uploads", {
      method: "POST",
      body: formData,
    });

    const res = await POST(req);
    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.saved[0]).toBe("dup-1.txt");
  });
});

describe("GET /api/uploads/[name]", () => {
  it("downloads an existing file", async () => {
    fs.writeFileSync(path.join(tmpUploadsDir, "download.csv"), "col1,col2\n1,2");

    const { GET } = await import("@/app/api/uploads/[name]/route");
    const res = await GET(new Request("http://localhost/api/uploads/download.csv"), {
      params: { name: "download.csv" },
    });

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/csv; charset=utf-8");
    expect(res.headers.get("content-disposition")).toContain("download.csv");
  });

  it("returns 404 for missing file", async () => {
    const { GET } = await import("@/app/api/uploads/[name]/route");
    const res = await GET(new Request("http://localhost/api/uploads/nope.txt"), {
      params: { name: "nope.txt" },
    });
    expect(res.status).toBe(404);
  });

  it("returns 400 for path traversal attempt", async () => {
    const { GET } = await import("@/app/api/uploads/[name]/route");
    const res = await GET(new Request("http://localhost/api/uploads/..%2Fetc%2Fpasswd"), {
      params: { name: "../etc/passwd" },
    });
    expect(res.status).toBe(400);
  });
});

describe("DELETE /api/uploads/[name]", () => {
  it("deletes an existing file", async () => {
    const filePath = path.join(tmpUploadsDir, "todelete.txt");
    fs.writeFileSync(filePath, "delete me");

    const { DELETE } = await import("@/app/api/uploads/[name]/route");
    const res = await DELETE(new Request("http://localhost/api/uploads/todelete.txt"), {
      params: { name: "todelete.txt" },
    });

    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(fs.existsSync(filePath)).toBe(false);
  });

  it("returns 404 for non-existent file", async () => {
    const { DELETE } = await import("@/app/api/uploads/[name]/route");
    const res = await DELETE(new Request("http://localhost/api/uploads/ghost.txt"), {
      params: { name: "ghost.txt" },
    });
    expect(res.status).toBe(404);
  });

  it("returns 400 for invalid filename", async () => {
    const { DELETE } = await import("@/app/api/uploads/[name]/route");
    const res = await DELETE(new Request("http://localhost/api/uploads/..%2Fhack"), {
      params: { name: "../hack" },
    });
    expect(res.status).toBe(400);
  });
});
