import { App } from "@modelcontextprotocol/ext-apps";


async function main() {
    const loading = document.getElementById("loading") as HTMLDivElement;
    const img = document.getElementById("image") as HTMLImageElement;
    const error = document.getElementById("error") as HTMLDivElement;

    const app = new App({ name: "Image Viewer", version: "1.0.0" });

    // app.ontoolresult = (result) => {
    //     loading.style.display = "none";

    //     const imageData = result.content?.find(c => c.type === "image");
    //     if (imageData) {
    //         img.src = "data:" + imageData.mimeType + ";base64," + imageData.data;
    //         img.style.display = "block";
    //         error.style.display = "none";
    //     } else {
    //         img.style.display = "none";
    //         error.textContent = "Failed to display image: No image data in result";
    //         error.style.display = "block";
    //     }
    // };

    await app.connect();

    const imageBuffer = await fetch("https://httpbin.org/image/png").then(r => r.arrayBuffer())
    const imageData = btoa(String.fromCharCode(...new Uint8Array(imageBuffer)))

    loading.style.display = "none";
    img.src = "data:image/png;base64," + imageData;
    img.style.display = "block";
    error.style.display = "none";
}

main()
