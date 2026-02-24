import { App } from "@modelcontextprotocol/ext-apps";
import { marked } from "marked";

async function main() {
    const loading = document.getElementById("loading") as HTMLDivElement;
    const error = document.getElementById("error") as HTMLDivElement;
    const container = document.getElementById("container") as HTMLDivElement;

    function displayError(msg: string) {
        console.error(msg)
        loading.style.display = "none";
        error.style.display = "block";
        error.textContent = msg;
    }

    const app = new App({ name: "File Viewer", version: "1.0.0" });

    app.ontoolresult = async (result) => {
        // TODO: Fix the sizing here, can use hostContext info do to so
        let hostContext = await app.getHostContext();

        let uri: string|undefined;
        try {
            uri = JSON.parse(result.content!.find((c) => c.type === "text")!.text).uri;
        } catch {}
        if (!uri) {
            console.error(`Invalid tool result:`, result)
            displayError(`Invalid tool result format.`);
            return
        }

        let response: Response;
        try {
            response = await fetch(uri);
        } catch (e) {
            displayError(`Failed to fetch file: ${e}`);
            return;
        }
        if (!response.ok) {
            displayError(`Failed to fetch file: ${response.status} ${response.statusText}`);
            return;
        }

        const contentType = response.headers.get("Content-Type") ?? "";

        container.innerHTML = ''; // clear container

        if (contentType.startsWith("image/") || /\.(png|jpg|jpeg)$/i.test(uri)) {
            const blob = await response.blob();
            const objectUrl = URL.createObjectURL(blob);
            const img = document.createElement("img");
            img.src = objectUrl;
            img.alt = uri;
            await new Promise<void>((resolve) => {
                img.complete ? resolve() : (img.onload = img.onerror = () => resolve());
            });
            container.appendChild(img);
        } else if (contentType.includes("application/pdf") || /\.pdf$/i.test(uri)) {
            const blob = await response.blob();
            const objectUrl = URL.createObjectURL(blob);
            const embed = document.createElement("embed");
            embed.src = objectUrl;
            embed.type = "application/pdf";
            container.appendChild(embed);
        } else if (contentType.startsWith("text/html") || /\.(htm|html)$/i.test(uri)) {
            const html = await response.text();
            const iframe = document.createElement("iframe");
            iframe.srcdoc = html;
            iframe.sandbox.add("allow-scripts");
            container.appendChild(iframe);
        } else if (contentType.startsWith("text/markdown") || /\.(md|markdown)$/i.test(uri)) {
            const text = await response.text();
            const div = document.createElement("div");
            div.className = "markdown";
            div.innerHTML = await marked(text);
            container.appendChild(div);
        } else {
            const text = await response.text();
            const pre = document.createElement("pre");
            pre.textContent = text;
            container.appendChild(pre);
        }

        loading.style.display = "none";
        container.style.display = "block";
        error.style.display = "none";
    };

    app.onhostcontextchanged = (newHostContext) => {
        // TODO
    }

    await app.connect();
}

main();
