import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import Shell from "./components/Shell";
import { ToastProvider } from "./components/ui";
import "./index.css";
import RequireAuth from "./pages/RequireAuth";
import Login from "./pages/Login";
import Today from "./pages/Today";
import Playground from "./pages/Playground";
import Approvals from "./pages/Approvals";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Audit from "./pages/Audit";
import System from "./pages/System";
import NotFound from "./pages/NotFound";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: true, retry: (n, err) => n < 2 && ![401, 403, 404, 410].includes(err?.status) },
  },
});

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  {
    element: <RequireAuth><Shell /></RequireAuth>,
    children: [
      { index: true, element: <Today /> },
      { path: "playground", element: <Playground /> },
      { path: "approvals", element: <Approvals /> },
      { path: "agents", element: <Agents /> },
      { path: "agents/:agentId", element: <AgentDetail /> },
      { path: "audit", element: <Audit /> },
      { path: "system", element: <System /> },
    ],
  },
  { path: "*", element: <NotFound /> },
]);

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
