#!/usr/bin/env node
import * as child_process from "child_process"
import * as fs from "fs"
import * as path from "path"

fs.rmSync("../src/vista_mcp_server/mcp-apps", {recursive: true, force: true});
for (const widget of fs.readdirSync(".", {withFileTypes: true})) {
    if (widget.name.endsWith(".html")) {
        // Vite's own script under this Node, not `npx`: on Windows that is
        // npx.cmd, which execFileSync cannot find without a shell.
        child_process.execFileSync(process.execPath, [path.join("node_modules", "vite", "bin", "vite.js"), "build"], {
            env: {...process.env, INPUT: path.join(widget.parentPath, widget.name)},
            encoding: 'utf-8',
        })
    }
}