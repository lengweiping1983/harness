/** Existing history and disclosure rows are not newly arriving conversation blocks. */
export function appendedBlockCount(previousTail: string | undefined, blocks: readonly { id: string }[]): number {
  if (previousTail === undefined) return 0
  const index = blocks.findIndex(block => block.id === previousTail)
  // A replaced snapshot has no reliable append boundary; do not invent unread activity.
  return index < 0 ? 0 : blocks.length - index - 1
}
