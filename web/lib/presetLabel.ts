/** Naming the weighting a result was scored on (pure; the hook that binds it to the
 * page's language and active profile is in labels.ts). */

const ADJUSTED = " (adjusted)";

/** A preset's label for the name the API returns ("balanced (adjusted)" when the weights
 * were changed). With the active profile given, a result scored on the profile's base
 * preset is named after the profile instead. */
export function presetLabel(
  presetName: (id: string) => string,
  preset: string,
  adjusted: string,
  profile?: { name: string; preset: string } | null,
): string {
  const base = preset.endsWith(ADJUSTED) ? preset.slice(0, -ADJUSTED.length) : preset;
  if (profile && profile.preset === base) return profile.name;
  return base === preset ? presetName(base) : `${presetName(base)} ${adjusted}`;
}
