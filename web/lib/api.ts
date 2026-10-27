/** REST calls to the Python API (things the page fetches directly, not via the assistant). */
import { API_URL } from "./config";
import type { AreaProfile, Comparison, Place, WeightProfile } from "./contracts.gen";

/** An error reply from the API, with the status so callers can tell "busy" (429) apart. */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}, params?: URLSearchParams): Promise<T> {
  const qs = params && params.size ? `?${params}` : "";
  const res = await fetch(`${API_URL}/api/v1${path}${qs}`, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(errorText(body.detail) ?? `HTTP ${res.status}`, res.status);
  return body as T;
}

/** The API's error detail as text (a validation error is a list of {loc, msg}). */
function errorText(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? `${(d as { loc?: unknown[] }).loc?.slice(-1)[0] ?? ""}: ${(d as { msg: string }).msg}` : String(d)))
      .join("; ");
  return detail == null ? undefined : JSON.stringify(detail);
}

function query(params: Record<string, string | undefined>, repeated: [string, string][] = []): URLSearchParams {
  const qs = new URLSearchParams(repeated);
  for (const [k, v] of Object.entries(params)) if (v !== undefined) qs.set(k, v);
  return qs;
}

const get = <T>(path: string, params: Record<string, string | undefined> = {}, repeated: [string, string][] = []) =>
  request<T>(path, {}, query(params, repeated));

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

/** A weights map as the API's JSON query parameter, or nothing when it is empty. */
const weightsParam = (weights?: Record<string, number>) =>
  weights && Object.keys(weights).length ? JSON.stringify(weights) : undefined;

/** `locale` (en, cy, gd, ga) picks the language of the area names in the answer. The
 * sliders' weights and the profile's multipliers make the card score and read
 * "(adjusted)" like the map. */
export const fetchProfile = (
  ref: string,
  preset?: string,
  locale?: string,
  themeWeights?: Record<string, number>,
  indicatorWeights?: Record<string, number>,
) =>
  get<AreaProfile>(`/areas/${encodeURIComponent(ref)}`, {
    preset,
    locale,
    theme_weights: weightsParam(themeWeights),
    indicator_weights: weightsParam(indicatorWeights),
  });

/** `locale` picks the language of the names and councils in the results (#187). */
export const searchPlaces = (q: string, locale?: string) => get<Place[]>("/search", { q, limit: "6", locale });

export const compareAreas = (
  codes: string[],
  preset?: string,
  locale?: string,
  themeWeights?: Record<string, number>,
  indicatorWeights?: Record<string, number>,
) =>
  get<Comparison>(
    "/compare",
    { preset, locale, theme_weights: weightsParam(themeWeights), indicator_weights: weightsParam(indicatorWeights) },
    codes.map((c) => ["areas", c]),
  );

/** A named weighting from a description of the user, in the interface language
 * (next-intl's "cy-GB" becomes the API's "cy"). */
export const generateProfile = (description: string, locale: string) =>
  post<WeightProfile>("/profile", { description, locale: locale.split("-")[0] });

export const runSql = (query: string) =>
  post<{ columns: string[]; rows: unknown[][]; truncated: boolean }>("/sql", { query });
