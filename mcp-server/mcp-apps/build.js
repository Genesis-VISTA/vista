#!/usr/bin/env node
import * as child_process from "child_process"
import * as fs from "fs"
import * as path from "path"

for (const widget of fs.readdirSync(".", {withFileTypes: true})) {
    if (widget.name.endsWith(".html")) {
        child_process.execFileSync("npx", ["vite", "build"], {
            env: {...process.env, INPUT: path.join(widget.parentPath, widget.name)},
            encoding: 'utf-8',
        })
    }
}