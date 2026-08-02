function normalizeOrigin(value: string): string | null {
  try {
    const parsed = new URL(value.trim());
    if (
      parsed.pathname !== '/' ||
      parsed.search ||
      parsed.hash ||
      parsed.username ||
      parsed.password
    ) return null;
    return parsed.origin;
  } catch {
    return null;
  }
}

export function isTrustedMutationOrigin(
  requestOrigin: string | null,
  requestUrlOrigin: string,
  configuredOrigins = ''
): boolean {
  if (!requestOrigin) return true;
  const normalizedRequest = normalizeOrigin(requestOrigin);
  if (!normalizedRequest) return false;

  const trusted = new Set<string>();
  const currentOrigin = normalizeOrigin(requestUrlOrigin);
  if (currentOrigin) trusted.add(currentOrigin);
  for (const configured of configuredOrigins.split(',')) {
    const origin = normalizeOrigin(configured);
    if (origin) trusted.add(origin);
  }
  return trusted.has(normalizedRequest);
}

