import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "node_modules/**", "public/sw.js", "next-env.d.ts"] },
  {
    rules: {
      // Listing photos come from arbitrary marketplace hosts; next/image would need an allowlist.
      "@next/next/no-img-element": "off",
    },
  },
];

export default config;
