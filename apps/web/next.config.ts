import type { NextConfig } from "next";
const config: NextConfig = {
  output: "standalone",
  distDir: process.env.ERJI_NEXT_DIST_DIR || ".next",
  compress: false,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${process.env.ERJI_API_ORIGIN || "http://127.0.0.1:18771"}/v1/:path*` }];
  },
};
export default config;
