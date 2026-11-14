import { expect, test } from 'vitest';
import { parseLength } from '@/lib/units';

test('lengths in metres or in feet and inches', () => {
  expect(parseLength('4.2')).toBe(4.2);
  expect(parseLength('4,2')).toBe(4.2);
  expect(parseLength(`12'-6"`)).toBeCloseTo(3.81, 6);
  expect(parseLength(`12'`)).toBeCloseTo(3.6576, 6);
  expect(parseLength('abc')).toBeNaN();
});
