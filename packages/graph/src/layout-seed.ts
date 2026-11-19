/**
 * Stable starting positions for the force layout.
 *
 * The same entity id always starts in the same place, so reopening a map, sharing a link or loading
 * a neighbourhood after the first one does not scramble what the reader already knows. The force
 * simulation moves nodes from these seeds; it never needs randomness of its own.
 */

function fnv1a(text: string, salt: number): number {
  let hash = (0x811c9dc5 ^ salt) >>> 0;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  // Final avalanche so similar ids (sequential uuids) do not land next to each other.
  hash ^= hash >>> 16;
  hash = Math.imul(hash, 0x85ebca6b) >>> 0;
  hash ^= hash >>> 13;
  hash = Math.imul(hash, 0xc2b2ae35) >>> 0;
  hash ^= hash >>> 16;
  return hash >>> 0;
}

/** A point in the unit disc, derived only from `id`. */
export function seedPosition(id: string): { x: number; y: number } {
  const angle = (fnv1a(id, 0x9e37) / 0x1_0000_0000) * 2 * Math.PI;
  // sqrt for a uniform spread over the disc rather than a cluster at the centre.
  const radius = Math.sqrt(fnv1a(id, 0x7f4a) / 0x1_0000_0000);
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}
