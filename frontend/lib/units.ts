/** A length typed by the user, in metres: "4.2", "4,2" or feet and inches as drawn on imperial sheets ("12'-6\"", "12'"). */
export function parseLength(text: string): number {
  const ft = String(text).match(/^\s*(\d+(?:\.\d+)?)\s*'\s*-?\s*(\d+(?:\.\d+)?)?\s*"?\s*$/);
  if (ft) return (Number(ft[1]) * 12 + Number(ft[2] ?? 0)) * 0.0254;
  return parseFloat(String(text).replace(',', '.'));
}
