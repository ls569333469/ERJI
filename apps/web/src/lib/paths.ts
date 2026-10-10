// Next.js Link/router prefix themselves; use only for raw browser URLs.
export const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
export function appPath(path: string): string {
  return basePath + path;
}
