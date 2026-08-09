import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ViewerApp } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("Viewer root element is missing.");

createRoot(root).render(
  <StrictMode>
    <ViewerApp />
  </StrictMode>,
);
