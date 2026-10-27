/**
 * Place names on the map. The basemap's own labels are hidden and replaced with ours, so
 * names use the app's ink and surface colours, one type scale from city to hamlet, and the
 * interface language. Both OpenFreeMap styles carry places in the "openmaptiles" source's
 * "place" layer, and the tile server offers Noto Sans in Regular, Bold and Italic.
 */
import type * as maplibregl from "maplibre-gl";

import { CHROME, type Mode } from "@/lib/palette";

const SOURCE = "openmaptiles";
export const LABEL_PREFIX = "lix-place-";

type Tone = "primary" | "secondary" | "muted";

interface Tier {
  id: string;
  classes: string[];
  font: string;
  minzoom: number;
  /** zoom, size pairs, interpolated linearly */
  size: number[];
  spacing: number;
  uppercase?: boolean;
  tone: Tone;
  halo: number;
}

const TIERS: Tier[] = [
  { id: "city", classes: ["city"], font: "Noto Sans Bold", minzoom: 4, size: [4, 10.5, 8, 13, 11, 15, 14, 17], spacing: 0.02, tone: "primary", halo: 1.6 },
  { id: "town", classes: ["town"], font: "Noto Sans Regular", minzoom: 7, size: [7, 10, 10, 12, 14, 14.5], spacing: 0.01, tone: "primary", halo: 1.5 },
  { id: "village", classes: ["village"], font: "Noto Sans Regular", minzoom: 10, size: [10, 10, 14, 12.5], spacing: 0.01, tone: "secondary", halo: 1.3 },
  { id: "district", classes: ["suburb", "quarter", "neighbourhood"], font: "Noto Sans Regular", minzoom: 12, size: [12, 9.5, 15, 11], spacing: 0.1, uppercase: true, tone: "secondary", halo: 1.3 },
  { id: "hamlet", classes: ["hamlet", "locality", "isolated_dwelling"], font: "Noto Sans Regular", minzoom: 13, size: [13, 9.5, 16, 11], spacing: 0.02, tone: "muted", halo: 1.2 },
];

/** The name in the interface language, else the Latin-script name every tile carries. */
export function nameField(locale: string): unknown {
  // The tiles key names by language alone ("name:cy"), the interface by BCP 47 ("cy-GB")
  const language = locale.split("-")[0];
  return ["coalesce", ["get", `name:${language}`], ["get", "name:latin"], ["get", "name"]];
}

function ink(mode: Mode, tone: Tone): string {
  const c = CHROME[mode];
  return tone === "primary" ? c.textPrimary : tone === "secondary" ? c.textSecondary : c.textMuted;
}

/**
 * Hides the basemap's place labels; countries and nations stay while zoomed right out.
 * The labels the basemap keeps (countries, seas, lakes, rivers, streets) read in the
 * interface language where the tiles carry it (#187).
 */
export function hideBasemapPlaces(map: maplibregl.Map, locale: string): void {
  const name = nameField(locale) as never;
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type !== "symbol" || layer.id.startsWith(LABEL_PREFIX)) continue;
    const sourceLayer = (layer as { "source-layer"?: string })["source-layer"];
    if (sourceLayer === "place") {
      if (/country|state|continent/.test(layer.id)) {
        map.setLayerZoomRange(layer.id, layer.minzoom ?? 0, Math.min(layer.maxzoom ?? 24, 6.5));
        map.setLayoutProperty(layer.id, "text-field", name);
      } else {
        map.setLayoutProperty(layer.id, "visibility", "none");
      }
      continue;
    }
    const field = map.getLayoutProperty(layer.id, "text-field");
    if (field && JSON.stringify(field).includes("name")) map.setLayoutProperty(layer.id, "text-field", name);
  }
}

/** Adds our place labels above everything else, or recolours them for the theme. */
export function placeLabels(map: maplibregl.Map, mode: Mode, locale: string): void {
  hideBasemapPlaces(map, locale);
  if (!map.getSource(SOURCE)) return;
  for (const tier of TIERS) {
    const id = LABEL_PREFIX + tier.id;
    const paint = {
      "text-color": ink(mode, tier.tone),
      "text-halo-color": CHROME[mode].page,
      "text-halo-width": tier.halo,
      "text-halo-blur": 0.6,
    };
    if (map.getLayer(id)) {
      map.setPaintProperty(id, "text-color", paint["text-color"]);
      map.setPaintProperty(id, "text-halo-color", paint["text-halo-color"]);
      map.setPaintProperty(id, "text-halo-width", paint["text-halo-width"]);
      continue;
    }
    map.addLayer({
      id,
      type: "symbol",
      source: SOURCE,
      "source-layer": "place",
      minzoom: tier.minzoom,
      filter: ["match", ["get", "class"], tier.classes, true, false],
      layout: {
        "text-field": nameField(locale) as never,
        "text-font": [tier.font],
        "text-size": ["interpolate", ["linear"], ["zoom"], ...tier.size] as never,
        "text-letter-spacing": tier.spacing,
        "text-transform": tier.uppercase ? "uppercase" : "none",
        "text-max-width": 8,
        "text-padding": 6,
        // Lower rank = more important, so a city wins the space over a suburb
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99] as never,
      },
      paint,
    });
  }
}

/** Switches every label to the interface language. */
export function setLabelLanguage(map: maplibregl.Map, locale: string): void {
  for (const tier of TIERS) {
    const id = LABEL_PREFIX + tier.id;
    if (map.getLayer(id)) map.setLayoutProperty(id, "text-field", nameField(locale) as never);
  }
}
