import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./index.css";
import App from "./App.tsx";

// Skip StrictMode as it causes errors in @mcp-ui/client AppRender
createRoot(document.getElementById("root")!).render(
  <TooltipProvider>
    <App />
  </TooltipProvider>
);
