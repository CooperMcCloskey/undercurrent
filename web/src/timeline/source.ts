// Where a day's data comes from. Days the API has a snapshot for (meta.dates) are real; every
// other day in the time bar's range comes from the illustrative archive (timeline/history.ts).
// When the backfill lands, meta.dates covers the range and the archive is never used.
import { api } from '../api/client'
import type { StoryDetail } from '../api/types.gen'
import { historyDetail, isHistoryStory } from './history'

/** How far back the time bar goes. */
export const HISTORY_YEARS = 5

/** A story's detail, from the archive or the API. */
export function loadStory(id: string): Promise<StoryDetail> {
  return isHistoryStory(id) ? Promise.resolve(historyDetail(id)) : api.story(id)
}
