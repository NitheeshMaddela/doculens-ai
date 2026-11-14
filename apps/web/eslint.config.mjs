import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import ts from "eslint-config-next/typescript";
export default defineConfig([...nextVitals, ...ts, globalIgnores([".next/**", "next-env.d.ts", "app/api-schema.ts", "test-results/**", "playwright-report/**"])]);
