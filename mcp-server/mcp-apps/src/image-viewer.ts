import { App } from "@modelcontextprotocol/ext-apps";

// Create app instance
const app = new App({ name: "Image Viewer", version: "1.0.0" });

// Handle tool results from the server. Set before `app.connect()` to avoid
// missing the initial tool result.
app.ontoolresult = (result) => {
    const loading = document.getElementById("loading") as HTMLDivElement;
    const img = document.getElementById("image") as HTMLImageElement;
    const error = document.getElementById("error") as HTMLDivElement;

    loading.style.display = "none";

    const imageData = result.content?.find(c => c.type === "image");
    if (imageData) {
        img.src = "data:" + imageData.mimeType + ";base64," + imageData.data;
        img.style.display = "block";
        error.style.display = "none";
    } else {
        img.style.display = "none";
        error.textContent = "Failed to display image: No image data in result";
        error.style.display = "block";
    }
};

app.connect();
