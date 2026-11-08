// Where a story's detail comes from: the API, which serves data/export/ (or the mock data until
// something has been exported). The time bar covers the days in `meta.dates`; there is no
// placeholder history any more.
import { api } from '../api/client'
import type { StoryDetail } from '../api/types.gen'

/** A story's detail. */
export function loadStory(id: string): Promise<StoryDetail> {
  return api.story(id)
}
