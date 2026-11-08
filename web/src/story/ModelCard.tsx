import { useState } from 'react'
import type { ModelMetrics } from '../api/types.gen'
import { beatsBaseline } from './forecast'

const factor = (log2: number) => `×${(2 ** log2).toFixed(2)}`

/**
 * How good the forecasts are: per horizon, the typical miss next to "it stays the size it is", and
 * how often the range held what happened. Horizons that don't beat the baseline are greyed out, and
 * the globe doesn't forecast them.
 */
export function ModelCard({ model, interval = 0.8 }: { model: ModelMetrics; interval?: number }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="pointer-events-auto relative">
      <button
        className="rounded bg-slate-800/80 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-700"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        How good is the forecast?
      </button>
      {open && (
        <div className="absolute left-0 top-7 z-20 w-96 rounded-lg bg-slate-900/95 p-3 text-xs shadow-xl">
          <p className="mb-2 text-slate-300">
            Tested on {model.test_start} to {model.test_end}. “Typical miss” is how far off the
            forecast usually was (×1 is perfect, ×2 is off by double). “{model.baseline_name}” is
            the forecast “the story stays as big as it is”.
          </p>
          <table className="w-full text-left tabular-nums">
            <thead className="text-slate-400">
              <tr>
                <th className="font-normal">Ahead</th>
                <th className="font-normal">Typical miss</th>
                <th className="font-normal">{model.baseline_name}</th>
                <th className="font-normal">Range held</th>
              </tr>
            </thead>
            <tbody>
              {model.horizons.map((h) => {
                const good = beatsBaseline(h)
                return (
                  <tr key={h.days} className={good ? '' : 'text-slate-500'}>
                    <td>{h.days} d</td>
                    <td className={good ? 'text-emerald-300' : ''}>{factor(h.mae_log2)}</td>
                    <td>{factor(h.baseline_mae_log2)}</td>
                    <td>
                      {Math.round(h.coverage * 100)}%{' '}
                      <span className="text-slate-500">of {Math.round(interval * 100)}%</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {model.horizons.some((h) => !beatsBaseline(h)) && (
            <p className="mt-2 text-slate-500">
              Greyed-out horizons don't beat the baseline, so they aren't shown as forecasts.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
