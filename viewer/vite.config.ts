import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    modulePreload: false,
    outDir: "dist-static",
    emptyOutDir: true,
    cssCodeSplit: false,
    sourcemap: false,
    rollupOptions: {
      output: {
        assetFileNames: "assets/app-[hash][extname]",
        entryFileNames: "assets/app-[hash].js",
        codeSplitting: false,
      },
    },
  },
});
