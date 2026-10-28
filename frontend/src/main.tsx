import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./fonts";
import "./styles.css";
import App from "./App";
import { SessionProvider } from "./auth/session";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SessionProvider>
      <App />
    </SessionProvider>
  </StrictMode>,
);
