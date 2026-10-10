import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";
const config: NextConfig = {
  output: "standalone",
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
  skipTrailingSlashRedirect: true,
  distDir: process.env.ERJI_NEXT_DIST_DIR || ".next",
  compress: false,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${process.env.ERJI_API_ORIGIN || "http://127.0.0.1:18771"}/v1/:path*` }];
  },
};
export default function nextConfig(phase: string): NextConfig {
  return {
    ...config,
    // Development must never consume output written by a production build.
    distDir: phase === PHASE_DEVELOPMENT_SERVER
      ? ".next-erji-dev"
      : config.distDir,
  };
}
