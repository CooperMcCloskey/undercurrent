import type { CSSProperties } from 'react'
import { KIND_STYLE, type SourceKind } from './articles'

/**
 * The mark for one kind of item, in the story's colour: a disc for news, a ring for social posts,
 * a diamond for Wikipedia. Fills its parent when `size` is omitted. Used for the dots on the globe
 * and for the matching marks in the panel, so the two read as the same thing.
 */
export function KindGlyph({
  kind,
  color,
  size,
}: {
  kind: SourceKind
  color: string
  size?: number
}) {
  const { shape } = KIND_STYLE[kind]
  const style: CSSProperties = {
    display: 'block',
    width: size ?? '100%',
    height: size ?? '100%',
    flexShrink: 0,
    boxSizing: 'border-box',
    ...(shape === 'disc' && { background: color, borderRadius: '50%' }),
    ...(shape === 'ring' && { border: `2px solid ${color}`, borderRadius: '50%' }),
    // `rotate`/`scale` rather than `transform`, so callers can still scale the mark.
    ...(shape === 'diamond' && {
      background: color,
      borderRadius: 2,
      rotate: '45deg',
      scale: 0.75,
    }),
  }
  return <span aria-hidden style={style} />
}
