import React from "react";
import ReactDOM from "react-dom/client";
import { Guide } from "./Guide";
import "../styles.css";
import "./docs.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Guide />
  </React.StrictMode>,
);
