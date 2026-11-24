import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = defineConfig([
  ...nextVitals,
  // Client code reaches the API through `apiUrl()` (lib/api-url.ts), so the
  // base path applies. Route handlers (`app/api/**`) are server code and name
  // backend paths directly.
  {
    files: ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}", "lib/**/*.{ts,tsx}"],
    ignores: ["app/api/**", "lib/api-url.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression:not([callee.name='apiUrl']) > Literal[value=/^\\/api\\//]",
          message: "Wrap API paths in apiUrl() from @/lib/api-url so the base path applies.",
        },
        {
          selector: "CallExpression:not([callee.name='apiUrl']) > TemplateLiteral > TemplateElement[value.raw=/^\\/api\\//]",
          message: "Wrap API paths in apiUrl() from @/lib/api-url so the base path applies.",
        },
      ],
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
