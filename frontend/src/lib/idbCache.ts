/**
 * IndexedDB-backed cache for API responses.
 *
 * Single object store (`api_cache`) keyed by endpoint path (e.g.
 * `"/api/training/status"`). Each record stores `{data, fetchedAt, endpoint}`.
 *
 * Part of Phase 1a of the offline-first dashboard work (see
 * `documentation/improvements/offline-first.md`). The goal is that any tab
 * using `useApi` can immediately render the last-known-good response on
 * mount, even if the backend is unreachable, and show a stale-data banner
 * while retrying in the background.
 *
 * We intentionally keep the schema tiny for v1 — one object store, one
 * `fetchedAt` timestamp per record. Phase 2 may add per-endpoint TTLs,
 * LRU eviction, or schema migrations. For now: clobber on write, no
 * eviction, one version.
 */

import { openDB, type IDBPDatabase, type DBSchema } from "idb";

export const DB_NAME = "alpha4gate-api-cache";
export const DB_VERSION = 1;
export const STORE_NAME = "api_cache";

/**
 * A single cached record. `data` is the parsed JSON body of a successful
 * response; `fetchedAt` is the epoch ms at which the fetch succeeded;
 * `endpoint` is the key (duplicated in the record so iterating the store
 * doesn't need separate key reads).
 */
export interface CacheRecord<T = unknown> {
  endpoint: string;
  data: T;
  fetchedAt: number;
}

/**
 * DBSchema for typed openDB() calls. The value type is deliberately
 * permissive (unknown) because different endpoints return different
 * shapes; callers cast via `useApi<T>`.
 */
interface ApiCacheSchema extends DBSchema {
  [STORE_NAME]: {
    key: string;
    value: CacheRecord<unknown>;
  };
}

/**
 * Lazy-opened singleton DB handle. Opening is async but only needs to
 * happen once per tab. Callers use `getDb()` which awaits the shared
 * promise so concurrent callers reuse the same connection.
 */
let dbPromise: Promise<IDBPDatabase<ApiCacheSchema>> | null = null;

function getDb(): Promise<IDBPDatabase<ApiCacheSchema>> {
  if (dbPromise === null) {
    dbPromise = openDB<ApiCacheSchema>(DB_NAME, DB_VERSION, {
      upgrade(db) {
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: "endpoint" });
        }
      },
    });
  }
  return dbPromise;
}

/**
 * Read the most recent cached value for an endpoint.
 *
 * Returns `null` if no value has ever been cached for this endpoint, if
 * the DB open fails (e.g. private browsing mode with IDB disabled), or
 * on any other read error. Callers treat `null` as "no cache, show
 * loading state."
 */
export async function readCache<T>(endpoint: string): Promise<CacheRecord<T> | null> {
  try {
    const db = await getDb();
    const record = await db.get(STORE_NAME, endpoint);
    return (record as CacheRecord<T> | undefined) ?? null;
  } catch (err) {
    // IDB can fail in private browsing, quota-exceeded, or disabled-by-policy
    // scenarios. We log once and fall back to "no cache" rather than crash
    // the component tree.
    console.warn(`idbCache.readCache(${endpoint}) failed:`, err);
    return null;
  }
}

/**
 * Write a fresh successful response to the cache.
 *
 * Overwrites any existing record for the same endpoint (v1 has no
 * history or versioning — the latest write wins). On write failure we
 * swallow the error: the component still has the live data in its
 * React state, so the immediate render is unaffected; we just lose
 * persistence for this fetch.
 */
export async function writeCache<T>(endpoint: string, data: T): Promise<void> {
  try {
    const db = await getDb();
    const record: CacheRecord<T> = {
      endpoint,
      data,
      fetchedAt: Date.now(),
    };
    await db.put(STORE_NAME, record as CacheRecord<unknown>);
  } catch (err) {
    console.warn(`idbCache.writeCache(${endpoint}) failed:`, err);
  }
}

/**
 * Remove a single cached entry. Used by tests and for manual cache
 * invalidation. Not currently called from production code paths.
 */
export async function deleteCache(endpoint: string): Promise<void> {
  try {
    const db = await getDb();
    await db.delete(STORE_NAME, endpoint);
  } catch (err) {
    console.warn(`idbCache.deleteCache(${endpoint}) failed:`, err);
  }
}

/**
 * Clear the entire cache. Exposed for a future "Clear offline cache"
 * button in the dashboard header. Not wired up in Phase 1a.
 */
export async function clearCache(): Promise<void> {
  try {
    const db = await getDb();
    await db.clear(STORE_NAME);
  } catch (err) {
    console.warn("idbCache.clearCache() failed:", err);
  }
}
