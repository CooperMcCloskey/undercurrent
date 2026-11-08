import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { Entity, TimelinePoint } from '../api/types.gen'
import { loadStory } from '../timeline/source'
import { rankArticles, type Article } from './articles'

const NONE: Article[] = []
const NO_TIMELINE: TimelinePoint[] = []
const NO_ENTITIES: Entity[] = []

/**
 * A story's items, ranked. Fetched when a story is first hovered or selected, then cached by
 * react-query, so hovering the same marker again is instant. Hover and the panel share one request.
 */
export function useArticles(storyId: string | undefined) {
  const query = useQuery({
    queryKey: ['story', storyId],
    queryFn: () => loadStory(storyId!),
    enabled: !!storyId,
  })
  const articles = useMemo(() => (query.data ? rankArticles(query.data.items) : NONE), [query.data])
  return {
    articles,
    summary: query.data?.summary ?? null,
    timeline: query.data?.timeline ?? NO_TIMELINE,
    entities: query.data?.entities ?? NO_ENTITIES,
    status: query.status,
    error: query.error,
    refetch: query.refetch,
  }
}
