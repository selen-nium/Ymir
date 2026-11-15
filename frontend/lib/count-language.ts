const NOUNS: Record<string, [string, string]> = {
  window: ['window', 'windows'], door: ['door', 'doors'], toilet_bowl: ['toilet', 'toilets'], sink: ['sink', 'sinks'],
  bathtub: ['bathtub', 'bathtubs'], shower: ['shower', 'showers'], wash_basin: ['washbasin', 'washbasins'],
};
export function countNoun(cls: string, n: number) {
  const name = String(cls).replaceAll('_', ' ');
  return (NOUNS[cls] ?? [name, `${name}${/(ch|sh|x)$/.test(name) ? 'es' : 's'}`])[n === 1 ? 0 : 1];
}
export const countSentence = (cls: string, n: number | null) => n == null
  ? `I couldn’t confirm the ${countNoun(cls, 1)} count. Please check the boxes.`
  : `I found ${n} ${countNoun(cls, n)}.`;
