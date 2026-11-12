/** Real microphone energy over 50 ms, mapped from −55…−15 dBFS to a visible envelope. */
export function createVoiceLevelMeter(sampleRate: number, onLevel: (level: number) => void) {
  const windowFrames = Math.max(1, Math.round(sampleRate / 20))
  let frames = 0, sum = 0, energy = 0, envelope = 0
  return (samples: Float32Array) => {
    for (const sample of samples) {
      const value = Number.isFinite(sample) ? Math.max(-1, Math.min(1, sample)) : 0
      sum += value
      energy += value * value
      if (++frames < windowFrames) continue
      // Remove DC offset so a constant device bias cannot look like speech.
      const rms = Math.sqrt(Math.max(0, energy / frames - (sum / frames) ** 2))
      const target = Math.max(0, Math.min(1, (20 * Math.log10(Math.max(rms, 1e-8)) + 55) / 40))
      envelope += (target - envelope) * (target > envelope ? .75 : .3)
      if (envelope < .005) envelope = 0
      onLevel(envelope)
      frames = sum = energy = 0
    }
  }
}
