import { t, useLocale } from '@/i18n'
import { memo } from 'react'
import type { ViewedImage } from '@/lib/toolImages'
import { useAppStore } from '@/store/app'

export const ImageGallery = memo(function ImageGallery({ images }: { images: ViewedImage[] }) {
  useLocale()

  const open = useAppStore((s) => s.openImageGallery)
  return <div role="group" aria-label={t("ui.components.chat.blocks.ImageGallery.f031615797fc", {p0: (images.length)})} className="ml-6 mb-2 flex flex-wrap gap-2">
    {images.map((image, index) => <button key={index} type="button" onClick={() => open(images, index)}
      aria-label={t("ui.components.chat.blocks.ImageGallery.0757bb5d4186", {p0: (index + 1), p1: (image.alt)})} title={image.alt}
      className="h-20 w-20 overflow-hidden rounded-lg border border-line bg-raised hover:border-brand focus-visible:ring-2 focus-visible:ring-brand">
      <img src={image.src} alt={image.alt} loading="lazy" decoding="async" className="h-full w-full object-cover" />
    </button>)}
  </div>
})
