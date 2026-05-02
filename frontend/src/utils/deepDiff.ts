/**
 * Deep-diff utility for comparing two arbitrary JSON objects — Step 7 of
 * the Models-tab build plan (CompareView).
 *
 * Returns a four-bucket result describing what changed between ``a`` and
 * ``b``: ``added`` (in ``b`` only), ``removed`` (in ``a`` only),
 * ``modified`` (in both, different value), and ``unchanged`` (in both,
 * equal value).
 *
 * Design notes:
 *
 *  - Pure function, no dependencies. Deterministic for the same inputs.
 *  - Recurses into plain objects (``Object.prototype.toString === "[object
 *    Object]"``) and into nested keys via dotted paths in the result.
 *  - Treats arrays as VALUES (not deep-diffed). Per the plan's reward-
 *    rules schema each rule is an object keyed by id with primitives or
 *    nested objects; arrays in hyperparams (e.g. ``layer_sizes:
 *    [128,128]``) are typically a single tunable knob, so the operator
 *    wants "is this array different?" rather than "which index changed?".
 *    Arrays are compared with ``JSON.stringify`` equality which is fine
 *    for our small JSON shapes.
 *  - Treats ``null`` as a value (not absent). ``undefined`` collapses to
 *    "key not present" — JSON.parse never returns ``undefined`` values
 *    so this only matters for hand-built fixtures.
 *  - Top-level non-object inputs collapse to a single ``modified`` /
 *    ``unchanged`` entry under the empty key — callers should pass
 *    objects in practice.
 *
 * Output shape:
 *
 * ```
 * {
 *   added:     { keyA: valueB,   ... }    // present in b only
 *   removed:   { keyA: valueA,   ... }    // present in a only
 *   modified:  { keyA: { a, b }, ... }    // present in both, different
 *   unchanged: { keyA: value,    ... }    // present in both, equal
 * }
 * ```
 *
 * Nested objects are flattened into dotted-path keys so the consumer
 * (``CompareView``'s diff renderer) can iterate a flat shape rather
 * than recursing again. This trades flexibility for ergonomics — the
 * Hyperparams and Reward-rules panels only need a flat add/remove/modify
 * list to render highlight rows.
 */

export interface DiffResult {
  /** Keys present in ``b`` but not in ``a``. Value is from ``b``. */
  added: Record<string, unknown>;
  /** Keys present in ``a`` but not in ``b``. Value is from ``a``. */
  removed: Record<string, unknown>;
  /** Keys present in both with different values. */
  modified: Record<string, { a: unknown; b: unknown }>;
  /** Keys present in both with equal values. */
  unchanged: Record<string, unknown>;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  if (v === null || v === undefined) return false;
  if (typeof v !== "object") return false;
  if (Array.isArray(v)) return false;
  // Reject Date, Map, Set, RegExp, etc. — only deep-diff plain JSON
  // objects. We use the prototype check rather than ``constructor.name
  // === "Object"`` so cross-realm objects (rare but possible in
  // jsdom test fixtures) still pass.
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

/**
 * Equality helper for non-recursive nodes (primitives + arrays).
 * Arrays use ``JSON.stringify`` for deterministic same-element-same-
 * order equality. ``NaN`` deliberately does NOT equal itself per
 * IEEE 754 — but since reward-rule schemas don't carry NaN we leave the
 * default JS semantics alone.
 */
function shallowEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  // Both non-array, non-object (or one of each) — already covered by ===
  // unless they're equivalent primitives (which === handles) or NaN
  // (which we ignore).
  return false;
}

function joinPath(prefix: string, key: string): string {
  if (prefix === "") return key;
  return `${prefix}.${key}`;
}

/**
 * Recursive worker. Mutates the four buckets in place keyed by dotted
 * paths.
 */
function diffInto(
  a: unknown,
  b: unknown,
  path: string,
  out: DiffResult,
): void {
  // Both plain objects → recurse key by key.
  if (isPlainObject(a) && isPlainObject(b)) {
    const keys = new Set<string>([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) {
      const subPath = joinPath(path, k);
      const aHas = Object.prototype.hasOwnProperty.call(a, k);
      const bHas = Object.prototype.hasOwnProperty.call(b, k);
      if (aHas && !bHas) {
        out.removed[subPath] = a[k];
        continue;
      }
      if (!aHas && bHas) {
        out.added[subPath] = b[k];
        continue;
      }
      // Both present — recurse.
      diffInto(a[k], b[k], subPath, out);
    }
    return;
  }

  // Mixed object / non-object at the same path → modified at this path.
  // (One side is an object, the other a primitive or array.)
  if (isPlainObject(a) !== isPlainObject(b)) {
    out.modified[path] = { a, b };
    return;
  }

  // Both are non-object (primitives, arrays, null) — leaf comparison.
  if (shallowEqual(a, b)) {
    out.unchanged[path] = a;
  } else {
    out.modified[path] = { a, b };
  }
}

/**
 * Compute a structural diff between two JSON-shaped values.
 *
 * See module doc for semantics and edge cases.
 */
export function deepDiff(a: unknown, b: unknown): DiffResult {
  const out: DiffResult = {
    added: {},
    removed: {},
    modified: {},
    unchanged: {},
  };
  diffInto(a, b, "", out);
  return out;
}
