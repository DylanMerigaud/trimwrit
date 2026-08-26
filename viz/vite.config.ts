/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Single self-contained dist/index.html, no external requests at runtime.
// The producer opens this file directly over file://, so every asset must be
// inlined (viteSingleFile) and nothing may reach a CDN or a font host.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: {
    target: "es2022",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
