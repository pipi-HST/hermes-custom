import { useEffect, useMemo, useState } from 'react'
import type { GardenCard } from '../../lib/api'
import { fetchGardenCards } from '../../lib/api'

const gardenKinds = ['viewpoint', 'fact', 'method', 'risk']

const gardenKindLabels: Record<string, string> = {
  viewpoint: '观点',
  fact: '事实',
  method: '方法',
  risk: '风险',
}

const gardenStatusLabels: Record<string, string> = {
  seed: '种子',
  accepted: '已接受',
  archived: '已归档',
}

export function useGardenCards(limit = 100) {
  const [cards, setCards] = useState<GardenCard[]>([])
  const [loading, setLoading] = useState(true)
  const [filterKind, setFilterKind] = useState('')
  const [filterStatus, setFilterStatus] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchGardenCards(limit)
      .then(items => {
        if (!cancelled) setCards(items)
      })
      .catch(() => {
        if (!cancelled) setCards([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [limit])

  const filteredCards = useMemo(() => {
    return cards.filter(card => {
      if (filterKind && card.card_kind !== filterKind) return false
      if (filterStatus && card.status !== filterStatus) return false
      return true
    })
  }, [cards, filterKind, filterStatus])

  return {
    cards,
    filteredCards,
    filterKind,
    filterStatus,
    kindLabels: gardenKindLabels,
    kinds: gardenKinds,
    loading,
    setFilterKind,
    setFilterStatus,
    statusLabels: gardenStatusLabels,
    statuses: Object.keys(gardenStatusLabels),
  }
}
