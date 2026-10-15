/** REST calls to the Python API (things the page fetches directly, not via the assistant). */
import { API_URL } from "./config";
import type { AreaProfile, Comparison, Place } from "./contracts.gen";

async function get<T>(
  path: string,
  params: Record<string, string | undefined> = {},
): Promise<T> {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined) as [
      string,
      string,
    ][],
  );
  const res = await fetch(`${API_URL}/api/v1${path}${qs.size ? `?${qs}` : ""}`);
  if (!res.ok)
    throw new Error(
      (await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`,
    );
  return res.json();
}

/** `locale` (en, cy, gd) picks the language of the area names in the answer. */
export const fetchProfile = (
  ref: string,
  preset?: string,
  locale?: string,
  themeWeights?: Record<string, number>,
) =>
  get<AreaProfile>(`/areas/${encodeURIComponent(ref)}`, {
    preset,
    locale,
    // The sliders' weights, so the card scores and names "(adjusted)" like the map
    ...(themeWeights && Object.keys(themeWeights).length ? { theme_weights: JSON.stringify(themeWeights) } : {}),
  });

export const searchPlaces = (q: string) =>
  get<Place[]>("/search", { q, limit: "6" });

export async function compareAreas(
  codes: string[],
  preset?: string,
  locale?: string,
): Promise<Comparison> {
  const qs = new URLSearchParams(codes.map((c) => ["areas", c]));
  if (preset) qs.set("preset", preset);
  if (locale) qs.set("locale", locale);
  const res = await fetch(`${API_URL}/api/v1/compare?${qs}`);
  if (!res.ok)
    throw new Error(
      (await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`,
    );
  return res.json();
}

export async function runSql(
  query: string,
): Promise<{ columns: string[]; rows: unknown[][]; truncated: boolean }> {
  const res = await fetch(`${API_URL}/api/v1/sql`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body;
}
