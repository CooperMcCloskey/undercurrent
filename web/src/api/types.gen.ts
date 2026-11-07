/* AUTO-GENERATED from pipeline/undercurrent/contracts.py by: npm run gen:types. Do not edit by hand. */

export interface UndercurrentContract {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Entity".
 */
export interface Entity {
  name: string;
  kind: "person" | "organization" | "location" | "other";
  wikipedia_title: string | null;
  pageviews_7d: number | null;
  edits_7d: number | null;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Factor".
 */
export interface Factor {
  name: string;
  /**
   * Signed; positive pushes toward growth
   */
  contribution: number;
}
/**
 * One place a story is happening. A story's footprint (several of these) is how far it
 * spreads on the heatmap. Build it from the story's GDELT location mentions.
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "FootprintPoint".
 */
export interface FootprintPoint {
  lat: number;
  lon: number;
  /**
   * Share of the story's attention here; sums to 1
   */
  weight: number;
  /**
   * How far this mention spreads. Use GDELT's location type: city ~75, state/region ~250, whole country ~half its width (300-1000)
   */
  radius_km: number;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Forecast".
 */
export interface Forecast {
  /**
   * P(volume over next horizon_days >= GROWTH_RATIO x previous horizon_days)
   */
  p_grow: number;
  horizon_days: number;
  model_version: string;
  factors: Factor[];
}
/**
 * One piece of media attached to a story.
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Item".
 */
export interface Item {
  id: string;
  kind: "news" | "bluesky" | "reddit" | "wikipedia";
  /**
   * Headline, or post text truncated to ~280 chars
   */
  title: string;
  url: string;
  published_at: string;
  /**
   * Domain, Bluesky handle or subreddit
   */
  source: string | null;
  language: string | null;
  /**
   * Cosine similarity to the story centroid
   */
  similarity: number;
}
/**
 * Message on the /api/live websocket.
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "LivePost".
 */
export interface LivePost {
  item: Item;
  story_id: string | null;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Location".
 */
export interface Location {
  name: string;
  /**
   * ISO 3166-1 alpha-2. GDELT uses FIPS 10-4 codes (UK, GM, CH...): convert in ingest.
   */
  country: string | null;
  lat: number;
  lon: number;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Meta".
 */
export interface Meta {
  /**
   * Days that have a snapshot, ascending
   */
  dates: string[];
  latest: string;
  model: ModelMetrics | null;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "ModelMetrics".
 */
export interface ModelMetrics {
  model_version: string;
  test_start: string;
  test_end: string;
  n_test: number;
  auc: number;
  brier: number;
  baseline_name: string;
  baseline_auc: number;
  baseline_brier: number;
}
/**
 * What actually happened. Only present on past snapshots (backtest / replay).
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Outcome".
 */
export interface Outcome {
  /**
   * Volume next horizon_days / previous horizon_days
   */
  growth_ratio: number;
  grew: boolean;
}
/**
 * Every story as of one day. Computed using only data up to and including as_of.
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Snapshot".
 */
export interface Snapshot {
  as_of: string;
  stories: StorySummary[];
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "StorySummary".
 */
export interface StorySummary {
  /**
   * Stable across days
   */
  id: string;
  title: string;
  location: Location1;
  /**
   * What the story is about; sets its colour
   */
  category: "politics" | "conflict" | "economy" | "society" | "disaster" | "health" | "technology" | "other";
  /**
   * Where the story is happening, for the heatmap. Empty means just `location`.
   */
  footprint: FootprintPoint[];
  article_count_7d: number;
  post_count_7d: number;
  /**
   * log2(volume last 7 days / volume previous 7 days)
   */
  growth_7d: number;
  /**
   * Mean GDELT tone of the story's articles
   */
  tone: number | null;
  first_seen: string;
  forecast: Forecast | null;
  outcome: Outcome | null;
}
/**
 * Main location: anchor for the marker and label
 */
export interface Location1 {
  name: string;
  /**
   * ISO 3166-1 alpha-2. GDELT uses FIPS 10-4 codes (UK, GM, CH...): convert in ingest.
   */
  country: string | null;
  lat: number;
  lon: number;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "StoryDetail".
 */
export interface StoryDetail {
  id: string;
  title: string;
  summary: string | null;
  first_seen: string;
  locations: Location[];
  entities: Entity[];
  timeline: TimelinePoint[];
  items: Item[];
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "TimelinePoint".
 */
export interface TimelinePoint {
  day: string;
  articles: number;
  posts: number;
  wiki_pageviews: number | null;
}
