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
   * Signed, in log2 units of the projected change in volume; positive pushes the projection up
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
 * Where a story is expected to be over the next few days: one projection per horizon.
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Forecast".
 */
export interface Forecast {
  model_version: string;
  /**
   * Share of outcomes that should fall between each horizon's low and high (0.8 = the 10th to 90th percentile)
   */
  interval: number;
  /**
   * Strictly ascending by days; every story in a snapshot uses a subset of Meta.horizons
   *
   * @minItems 1
   */
  horizons: [HorizonForecast, ...HorizonForecast[]];
}
/**
 * A story's projected size `days` days after the snapshot's `as_of`.
 *
 * The unit is `StorySummary.article_count_7d`: the number the globe draws a story's heat from.
 * So the projection for day t+h is drawn by the same code as the real snapshot of day t+h. To
 * look ahead, take the story from the snapshot of day t and swap `article_count_7d` for
 * `articles_7d`. The model is judged on the same number (see `Outcome`).
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "HorizonForecast".
 */
export interface HorizonForecast {
  /**
   * How many days after the snapshot's as_of
   */
  days: number;
  /**
   * Median projection of article_count_7d on day as_of + days: articles published in the 7 days ending that day
   */
  articles_7d: number;
  /**
   * Lower end of the central `Forecast.interval` range
   */
  low: number;
  /**
   * Upper end of the central `Forecast.interval` range
   */
  high: number;
  /**
   * Top drivers of this projection; may be empty
   */
  factors: Factor[];
}
/**
 * How well the projections `days` ahead did on the held-out test days.
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "HorizonMetrics".
 */
export interface HorizonMetrics {
  days: number;
  /**
   * (story, day) pairs scored
   */
  n_test: number;
  /**
   * Mean |log2((projected + 1) / (actual + 1))|: the typical miss as a factor of 2^x (0 = perfect, 1 = off by 2x)
   */
  mae_log2: number;
  /**
   * The same error for the baseline; the model earns its place by beating it
   */
  baseline_mae_log2: number;
  /**
   * Share of actuals inside [low, high]. Should be close to Forecast.interval; much lower means the ranges are overconfident
   */
  coverage: number;
}
/**
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "HorizonOutcome".
 */
export interface HorizonOutcome {
  days: number;
  /**
   * What article_count_7d actually read on as_of + days (0 if the story had gone quiet). Equals that day's snapshot whenever the story is in it.
   */
  articles_7d: number;
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
  /**
   * Every place this item mentions, the main one first (for a GDELT article: its most specific location first). The globe puts a dot at each of them, which is also why the story's heat sits where it does. Empty if unknown, e.g. most social posts.
   */
  locations: Location[];
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
 * via the `definition` "Meta".
 */
export interface Meta {
  /**
   * Days that have a snapshot, ascending
   */
  dates: string[];
  latest: string;
  /**
   * Forecast horizons in days, ascending: how far past `latest` the globe can look. Empty if there are no forecasts.
   */
  horizons: number[];
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
  /**
   * 'persistence' = the story stays as big as it is now
   */
  baseline_name: string;
  /**
   * One per horizon, ascending by days. A horizon is only worth showing as a forecast if mae_log2 < baseline_mae_log2.
   */
  horizons: HorizonMetrics[];
}
/**
 * What actually happened. Only present on past snapshots (backtest / replay), and only for
 * the horizons whose day is on or before Meta.latest.
 *
 * This interface was referenced by `UndercurrentContract`'s JSON-Schema
 * via the `definition` "Outcome".
 */
export interface Outcome {
  /**
   * Strictly ascending by days; a subset of the forecast's horizons
   *
   * @minItems 1
   */
  horizons: [HorizonOutcome, ...HorizonOutcome[]];
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
  /**
   * Where the story is expected to be in the coming days. None = no projection (too little history): the future globe then draws it unchanged.
   */
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
