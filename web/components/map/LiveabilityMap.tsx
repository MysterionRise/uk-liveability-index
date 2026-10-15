"use client";

/**
 * The choropleth: local authorities (zoomed out) → MSOAs → LSOAs (zoomed in), from
 * static PMTiles. Polygons carry only their code; colours come from feature-state, so
 * changing weights recolours instantly without new tiles. The assistant moves the map,
 * outlines areas and drops markers through the shared state.
 */
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";
import { useEffect, useRef, useState } from "react";

import { useData, useLiveability, useScores } from "@/components/AppData";
import { MapLegend } from "@/components/map/MapLegend";
import { LABEL_PREFIX, placeLabels, setLabelLanguage } from "@/components/map/placeLabels";
import { useTheme } from "@/components/ThemeProvider";
import { fillColorExpression } from "@/lib/colors";
import { absoluteDataUrl } from "@/lib/config";
import { pctRank } from "@/lib/format";
import { CHROME, FILL_OPACITY } from "@/lib/palette";
import { useLocale, useTranslations } from "next-intl";

import { useCoverageName } from "@/lib/copy";
import { activeBbox } from "@/lib/data";
import { bboxOf } from "@/lib/state";

const LAYERS = [
  { id: "lad", key: "lad_cd", minzoom: 0, maxzoom: 7 },
  { id: "msoa", key: "msoa21cd", minzoom: 7, maxzoom: 9.5 },
  { id: "lsoa", key: "lsoa21cd", minzoom: 9.5, maxzoom: 24 },
] as const;

// Until the manifest arrives: the UK
const UK: [number, number, number, number] = [-8.7, 49.8, 1.8, 60.9];

let protocolAdded = false;

function basemap(dark: boolean): string {
  return `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`;
}

const OVERLAY_SOURCES = new Set<string>([...LAYERS.map((l) => l.id), "pois"]);

function ours(layerId: string): boolean {
  return layerId === "pois" || layerId.startsWith(LABEL_PREFIX) || LAYERS.some((l) => layerId.startsWith(`${l.id}-`));
}

/** Where fills and borders go: above the basemap's roads and boundaries, under its labels. */
function fillSlot(layers: { id: string; type: string }[]): number {
  // The first label after the last non-symbol layer. (The dark style has a water label
  // before its roads, so "the first symbol layer" would put the fills under the roads.)
  return layers.findLastIndex((l) => l.type !== "symbol") + 1;
}

/** Keeps our sources and layers through a basemap swap, so the choropleth never blanks. */
function carryOverlay(
  previous: maplibregl.StyleSpecification | undefined,
  next: maplibregl.StyleSpecification,
): maplibregl.StyleSpecification {
  if (!previous) return next;
  const sources = { ...next.sources };
  for (const [id, source] of Object.entries(previous.sources)) if (OVERLAY_SOURCES.has(id)) sources[id] = source;
  const carried = previous.layers.filter((l) => ours(l.id));
  const under = carried.filter((l) => l.type === "fill" || (l.type === "line" && !l.id.endsWith("-highlight")));
  const over = carried.filter((l) => !under.includes(l));
  const slot = fillSlot(next.layers);
  return { ...next, sources, layers: [...next.layers.slice(0, slot), ...under, ...next.layers.slice(slot), ...over] };
}

/** Adds our sources and layers once per style, and (re)applies the theme's colours. */
function addOverlay(map: maplibregl.Map, dark: boolean): void {
  const chrome = CHROME[dark ? "dark" : "light"];
  if (!map.getSource("lsoa")) addOverlayLayers(map, chrome);
  for (const layer of LAYERS) {
    map.setPaintProperty(`${layer.id}-fill`, "fill-color", fillColorExpression(dark) as never);
    map.setPaintProperty(`${layer.id}-line`, "line-color", chrome.surface);
    map.setPaintProperty(`${layer.id}-highlight`, "line-color", chrome.accent);
  }
  map.setPaintProperty("pois", "circle-color", chrome.accent);
  map.setPaintProperty("pois", "circle-stroke-color", chrome.surface);
}

function addOverlayLayers(map: maplibregl.Map, chrome: (typeof CHROME)["light"]): void {
  const layers = map.getStyle().layers ?? [];
  const firstSymbol = layers[fillSlot(layers)]?.id;
  for (const layer of LAYERS) {
    map.addSource(layer.id, {
      type: "vector",
      url: `pmtiles://${absoluteDataUrl()}/tiles/${layer.id}.pmtiles`,
      promoteId: { [layer.id]: layer.key },
      attribution: "Contains OS data © Crown copyright and database right; ONS",
    });
    map.addLayer(
      {
        id: `${layer.id}-fill`,
        type: "fill",
        source: layer.id,
        "source-layer": layer.id,
        minzoom: layer.minzoom,
        maxzoom: layer.maxzoom,
        paint: { "fill-opacity": FILL_OPACITY[layer.id] },
      },
      firstSymbol,
    );
    map.addLayer(
      {
        id: `${layer.id}-line`,
        type: "line",
        source: layer.id,
        "source-layer": layer.id,
        minzoom: layer.minzoom,
        maxzoom: layer.maxzoom,
        paint: { "line-color": chrome.surface, "line-width": 0.3, "line-opacity": 0.6 },
      },
      firstSymbol,
    );
    // Outlines stay visible when zoomed past the layer's own band (an MSOA picked from a
    // ranking is still outlined once its LSOAs are showing)
    map.addLayer({
      id: `${layer.id}-highlight`,
      type: "line",
      source: layer.id,
      "source-layer": layer.id,
      minzoom: layer.minzoom,
      filter: ["in", ["get", layer.key], ["literal", []]],
      paint: { "line-color": chrome.accent, "line-width": 2.5 },
    });
  }
  map.addSource("pois", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "pois",
    type: "circle",
    source: "pois",
    paint: {
      "circle-radius": 6,
      "circle-color": chrome.accent,
      "circle-stroke-width": 2,
      "circle-stroke-color": chrome.surface,
    },
  });
}

export function LiveabilityMap() {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  // Bumped on every style load: re-adding sources clears feature-state, so the
  // colouring effect must re-run even if "ready" flips false→true within one render
  const [styleVersion, setStyleVersion] = useState(0);
  const ready = styleVersion > 0;
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null);
  const dark = useTheme().mode === "dark";
  const locale = useLocale();
  const { manifest, scores } = useData();
  const { state, update } = useLiveability();
  // Read inside map event handlers, which outlive a render
  const compareRef = useRef(state.compare_within);
  useEffect(() => {
    compareRef.current = state.compare_within;
  }, [state.compare_within]);
  const t = useTranslations("Map");
  const tLegend = useTranslations("Legend");
  const coverage = useCoverageName();
  const against = tLegend(`against.${state.compare_within}`, { coverage });
  const values = useScores(state);
  const appliedBbox = useRef<string>("");
  const styleDark = useRef<boolean | null>(null);

  const darkRef = useRef(dark);
  useEffect(() => {
    darkRef.current = dark;
  }, [dark]);
  const localeRef = useRef(locale);
  const manifestRef = useRef(manifest);
  // The build's nations decide the opening view; fit once the manifest is in, unless a
  // shared link or the assistant already set a view
  useEffect(() => {
    manifestRef.current = manifest;
    const map = mapRef.current;
    if (map && manifest && !state.map.bbox && !appliedBbox.current) {
      map.fitBounds(activeBbox(manifest), { animate: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest]);

  // Create the map; every time a basemap style loads, (re)add our sources and layers
  useEffect(() => {
    if (!container.current) return;
    if (!protocolAdded) {
      maplibregl.addProtocol("pmtiles", new Protocol().tile);
      // Copied from node_modules by scripts/copy-maplibre-worker.mjs
      maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");
      protocolAdded = true;
    }
    const map = new maplibregl.Map({
      container: container.current,
      style: basemap(darkRef.current),
      bounds: manifestRef.current ? activeBbox(manifestRef.current) : UK,
      attributionControl: { compact: true },
    });
    styleDark.current = darkRef.current;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    // MapLibre logs every failed tile request, including the range requests the browser
    // cancels when the view moves on; those are not errors (#118)
    map.on("error", (e) => {
      const err = (e as unknown as { error?: { name?: string; message?: string } }).error;
      if (err && (err.name === "AbortError" || /abort|cancel|Failed to fetch/i.test(err.message ?? ""))) return;
      console.error(err ?? e);
    });
    // The compact attribution opens expanded on load and would sit under the legend on
    // a phone until the first drag; start it collapsed, the licence a tap away (#114)
    map.once("load", () => {
      const attrib = container.current?.querySelector(".maplibregl-ctrl-attrib");
      if (attrib?.classList.contains("maplibregl-compact-show")) {
        attrib.classList.remove("maplibregl-compact-show");
        attrib.querySelector("button")?.setAttribute("aria-expanded", "false");
      }
    });
    map.on("style.load", () => {
      addOverlay(map, darkRef.current);
      placeLabels(map, darkRef.current ? "dark" : "light", localeRef.current);
      setStyleVersion((v) => v + 1);
    });
    mapRef.current = map;
    // Exposed for end-to-end tests and debugging (not in production builds unless asked)
    if (process.env.NODE_ENV !== "production" || process.env.NEXT_PUBLIC_EXPOSE_MAP === "1") {
      (window as unknown as { __map: unknown }).__map = map;
    }
    return () => {
      setStyleVersion(0);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Swap the basemap when the theme changes (the style.load handler re-adds the overlay)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || styleDark.current === dark) return;
    styleDark.current = dark;
    map.setStyle(basemap(dark), { transformStyle: carryOverlay });
  }, [dark]);

  // Place names follow the interface language
  useEffect(() => {
    localeRef.current = locale;
    const map = mapRef.current;
    if (map && ready) setLabelLanguage(map, locale);
  }, [locale, ready]);

  // Colour every area for the current weights and layer
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !values || !scores || !map.getSource("lsoa")) return;
    performance.mark("lix-recolour-start");
    for (let i = 0; i < scores.n; i++) {
      const v = values.lsoa[i];
      map.setFeatureState(
        { source: "lsoa", sourceLayer: "lsoa", id: scores.codes[i] },
        { v: Number.isNaN(v) ? null : v },
      );
    }
    for (const [code, v] of values.msoa) map.setFeatureState({ source: "msoa", sourceLayer: "msoa", id: code }, { v });
    for (const [code, v] of values.lad) map.setFeatureState({ source: "lad", sourceLayer: "lad", id: code }, { v });
    performance.measure("lix-recolour", "lix-recolour-start");
  }, [ready, styleVersion, values, scores]);

  // Outline highlighted and selected areas
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const codes = [...state.map.highlighted, ...(state.map.selected ? [state.map.selected] : [])];
    for (const layer of LAYERS) {
      map.setFilter(`${layer.id}-highlight`, ["in", ["get", layer.key], ["literal", codes]]);
    }
  }, [ready, styleVersion, state.map.highlighted, state.map.selected]);

  // Markers for places the assistant found
  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource("pois") as maplibregl.GeoJSONSource | undefined;
    if (!map || !ready || !source) return;
    source.setData({
      type: "FeatureCollection",
      features: state.map.pois.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.point.lon, p.point.lat] },
        properties: { name: p.name ?? p.category, category: p.category },
      })),
    });
  }, [ready, styleVersion, state.map.pois]);

  // Fly to the area the assistant (or a list click) asked for
  useEffect(() => {
    const map = mapRef.current;
    const bbox = bboxOf(state.map.bbox);
    if (!map || !bbox) return;
    const key = bbox.join(",");
    if (key === appliedBbox.current) return;
    appliedBbox.current = key;
    map.fitBounds(
      [
        [bbox[0], bbox[1]],
        [bbox[2], bbox[3]],
      ],
      { padding: 60, maxZoom: 14, duration: 800 },
    );
  }, [state.map.bbox]);

  // Click to select; hover for the value
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const fills = LAYERS.map((l) => `${l.id}-fill`);
    const onClick = (e: maplibregl.MapMouseEvent) => {
      const f = map.queryRenderedFeatures(e.point, { layers: fills })[0];
      if (!f) return;
      if (f.layer.id === "lsoa-fill") {
        const code = String(f.properties.lsoa21cd);
        update((s) => ({ ...s, map: { ...s.map, selected: code } }));
      } else {
        map.easeTo({ center: e.lngLat, zoom: f.layer.id === "lad-fill" ? 8 : 10.5 });
      }
    };
    const onMove = (e: maplibregl.MapMouseEvent) => {
      const f = map.queryRenderedFeatures(e.point, { layers: fills })[0];
      map.getCanvas().style.cursor = f ? "pointer" : "";
      const v = f?.state?.v;
      const name = f?.properties?.name;
      // The wording follows "compare against": the value shown is already within the
      // nation or the urban/rural class when that is chosen (#113)
      const key =
        compareRef.current === "nation" ? "hoverNation" : compareRef.current === "urban_rural" ? "hoverUrbanRural" : "hover";
      setHover(
        f && typeof v === "number"
          ? { x: e.point.x, y: e.point.y, text: name ? t(key, { name, pct: pctRank(v) }) : t(`${key}NoName`, { pct: pctRank(v) }) }
          : null,
      );
    };
    map.on("click", onClick);
    map.on("mousemove", onMove);
    map.on("mouseout", () => setHover(null));
    return () => {
      map.off("click", onClick);
      map.off("mousemove", onMove);
    };
  }, [ready, update, t]);

  return (
    <div className="relative h-full w-full" data-testid="map">
      <div ref={container} className="h-full w-full" />
      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1 text-xs text-[var(--text-primary)] shadow"
          style={{ left: hover.x + 12, top: hover.y + 12 }}
        >
          {hover.text}
        </div>
      )}
      {values && <MapLegend label={values.label} against={against} dark={dark} />}
    </div>
  );
}
