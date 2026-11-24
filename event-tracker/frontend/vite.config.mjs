import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, normalizePath } from "vite";
import { parseTopicExamples } from "./src/topic-example-catalog.ts";

const catalogPath = fileURLToPath(new URL("../docs/topic-examples.zh-CN.md", import.meta.url));
const virtualCatalog = "virtual:topic-examples";
const resolvedCatalog = "\0" + virtualCatalog;

export default defineConfig({
  plugins: [{
    name: "traceradar-topic-examples",
    resolveId(id) {
      return id === virtualCatalog ? resolvedCatalog : null;
    },
    load(id) {
      if (id !== resolvedCatalog) return null;
      this.addWatchFile(catalogPath);
      const catalog = parseTopicExamples(readFileSync(catalogPath, "utf8"));
      return `export default ${JSON.stringify(catalog)};`;
    },
    handleHotUpdate({ file, server }) {
      if (normalizePath(file) !== normalizePath(catalogPath)) return;
      const module = server.moduleGraph.getModuleById(resolvedCatalog);
      if (module) return [module];
    },
  }],
  server: { port: 5173, strictPort: true, proxy: { "/api": "http://127.0.0.1:8000" } },
});
