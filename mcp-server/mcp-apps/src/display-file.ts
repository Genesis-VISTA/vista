import { App, type McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { marked } from "marked";

async function renderResult(uri: string, hostContext: McpUiHostContext) {
    // TODO: Fix the sizing here, can use hostContext info do to so
    let response: Response;
    try {
        response = await fetch(uri);
    } catch (e) {
        throw Error(`Failed to fetch file: ${e}`);
    }
    if (!response.ok) {
        throw Error(`Failed to fetch file: ${response.status} ${response.statusText}`);
    }
    const contentType = response.headers.get("Content-Type") ?? "";
    const content = document.createElement("div");

    if (contentType.startsWith("image/") || /\.(png|jpg|jpeg)$/i.test(uri)) {
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const img = document.createElement("img");
        img.src = objectUrl;
        img.alt = uri;
        await new Promise<void>((resolve) => {
            img.complete ? resolve() : (img.onload = img.onerror = () => resolve());
        });
        content.appendChild(img);
    } else if (contentType.includes("application/pdf") || /\.pdf$/i.test(uri)) {
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const embed = document.createElement("embed");
        embed.src = objectUrl;
        embed.type = "application/pdf";
        content.appendChild(embed);
    } else if (contentType.startsWith("text/html") || /\.(htm|html)$/i.test(uri)) {
        const html = await response.text();
        const iframe = document.createElement("iframe");
        iframe.srcdoc = html;
        iframe.sandbox.add("allow-scripts");
        content.appendChild(iframe);
    } else if (contentType.startsWith("text/markdown") || /\.(md|markdown)$/i.test(uri)) {
        const text = await response.text();
        const div = document.createElement("div");
        div.className = "markdown";
        div.innerHTML = await marked(text);
        content.appendChild(div);
    } else {
        const text = await response.text();
        const pre = document.createElement("pre");
        pre.textContent = text;
        content.appendChild(pre);
    }

    return content;
}

async function main() {
    const app = new App({ name: "File Viewer", version: "1.0.0" });

    // ontoolresult can get called multiple times for streaming results.
    // Track this so we only keep the last entry, regardless of when promises resolve.
    let generation = 0;

    app.ontoolresult = async (result) => {
        const myGen = ++generation;
        let error: string|undefined;
        let content: HTMLElement|undefined;

        let uri: string|undefined;
        try {
            uri = JSON.parse(result.content!.find((c) => c.type === "text")!.text).uri;
        } catch {}
        if (!uri) {
            console.error(`Invalid tool result:`, result)
            error = "Invalid tool result format."
        } else {
            try {
                content = await renderResult(uri, app.getHostContext() ?? {});
            } catch (e) {
                error = `${e}`;
            }
        }

        const loadingElem = document.getElementById("loading") as HTMLDivElement;
        const errorElem = document.getElementById("error") as HTMLDivElement;
        const containerElem = document.getElementById("container") as HTMLDivElement;

        if (myGen != generation) return;

        if (error) {
            console.error(error)
            loadingElem.style.display = "none";
            errorElem.style.display = "block";
            errorElem.textContent = error;
        } else {
            containerElem.innerHTML = "";
            containerElem.append(...content!.childNodes);
            loadingElem.style.display = "none";
            containerElem.style.display = "block";
            errorElem.style.display = "none";
        }
    };

    app.onhostcontextchanged = (newHostContext) => {
        // TODO
    }

    await app.connect();
}

main();
