import { buildApp, buildDeps, shutdown } from "./app.js";
import { loadConfig } from "./config.js";

const cfg = loadConfig();
const deps = buildDeps(cfg);
const app = await buildApp(deps);
deps.control.onKill = (ws, stopped, via) => app.log.warn({ ws, stopped, via }, "fleet kill switch changed");
await deps.control.start();
await app.listen({ port: cfg.PORT, host: cfg.HOST });

for (const sig of ["SIGTERM", "SIGINT"] as const) {
  process.on(sig, () => {
    app.log.info({ sig }, "shutting down");
    void shutdown(app, deps).then(() => process.exit(0));
  });
}
