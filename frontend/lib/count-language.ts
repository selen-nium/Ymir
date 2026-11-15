const NOUNS: Record<string, [string, string]> = {
  window: ['window', 'windows'], door: ['door', 'doors'], toilet_bowl: ['toilet', 'toilets'], sink: ['sink', 'sinks'],
  'folding door': ['folding door', 'folding doors'],
  door_single: ['single door', 'single doors'], door_double: ['double door', 'double doors'],
  door_sliding: ['sliding door', 'sliding doors'], cooker_stove: ['stove', 'stoves'],
  'bathtubs and showers': ['bathtub or shower', 'bathtubs and showers'],
  'showers and bathtubs': ['shower or bathtub', 'showers and bathtubs'],
  bathtub: ['bathtub', 'bathtubs'], shower: ['shower', 'showers'], wash_basin: ['washbasin', 'washbasins'],
};
export function countNoun(cls: string, n: number, classes?: string[]): string {
  if (classes && classes.length > 1)
    return classes.map(c => countNoun(c,n)).join(n === 1 ? ' or ' : ' and ') + (n === 1 ? '' : ' in total');
  const name = String(cls).replaceAll('_', ' ');
  const known = NOUNS[cls] ?? Object.values(NOUNS).find(forms => forms.includes(name));
  const plural = /s$/.test(name) && !/(ss|us|is)$/.test(name) ? name : `${name}${/(ch|sh|x)$/.test(name) ? 'es' : 's'}`;
  return (known ?? [name, plural])[n === 1 ? 0 : 1];
}
export const countSentence = (cls: string, n: number | null, classes?: string[]) => n == null
  ? `I couldn’t confirm the ${countNoun(cls, 1, classes)} count. Please check the boxes.`
  : `I found ${n} ${countNoun(cls, n, classes)}${(!classes || classes.length < 2) && n !== 1 && ['bathtubs and showers', 'showers and bathtubs'].includes(cls) ? ' in total' : ''}.`;
