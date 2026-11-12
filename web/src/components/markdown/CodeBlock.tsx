import { usePageVisible } from '@/hooks/usePageVisible'
import { t, useLocale } from '@/i18n'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Copy, Eye, WrapText } from 'lucide-react'
import { useAppStore } from '@/store/app'
import { cn, copyText } from '@/lib/utils'
import { normalizeLang } from '@/lib/highlight'
import { normalizeLang as fenceLanguage } from '@/lib/streaming'
import { scheduleCodeHighlight } from '@/lib/codeHighlight'
import { IconButton, Tooltip } from '@/components/ui/primitives'
import { HtmlSandboxFrame } from '@/components/ui/HtmlSandboxFrame'
import { Mermaid } from './Mermaid'

/**
 * 代码块。
 *
 * 三件事让它"像个正经产品"而不是裸 `<pre>`：
 * 1. **按需高亮** —— highlight.js 只在语言命中时加载对应语言包，
 *    未知语言直接降级成纯文本，不做全量注册（否则首包 +~1MB）。
 * 2. **横向溢出提示** —— 右侧渐隐 + 可切换自动换行，长行也能读。
 * 3. **Mermaid 分流** —— ```mermaid 直接走图表渲染，不当代码显示。
 */

export interface CodeBlockProps {
  code: string
  language?: string
  filename?: string
  className?: string
  /** 内联代码不渲染外框与工具条。 */
  inline?: boolean
  previewReady?: boolean
}

export const CodeBlock = memo(function CodeBlock({
  code,
  language,
  className,
  inline,
  previewReady = true,
}: CodeBlockProps) {
  useLocale()
  const visible = usePageVisible()

  const lang = language ? normalizeLang(language) : ''
  const isMermaid = lang === 'mermaid'
  const isHtml = fenceLanguage(language) === 'html'
  const [preview, setPreview] = useState(isHtml && previewReady)
  useEffect(() => setPreview(isHtml && previewReady), [isHtml, previewReady])
  const [highlighted, setHighlighted] = useState<{code: string; lang: string; html: string | null} | null>(null)
  const html = highlighted?.code === code && highlighted.lang === lang ? highlighted.html : null
  const [copiedCode, setCopiedCode] = useState<string | null>(null)
  const copied = copiedCode === code
  const [wrap, setWrap] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const preRef = useRef<HTMLPreElement | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => timer.current && clearTimeout(timer.current), [])

  useEffect(() => {
    if (!visible || inline || isMermaid || (isHtml && previewReady && preview)) return
    return scheduleCodeHighlight(code, lang, html => setHighlighted({code, lang, html}))
  }, [code, lang, inline, isMermaid, visible, isHtml, previewReady, preview])

  useEffect(() => {
    const node = preRef.current
    if (!visible || !node || (isHtml && previewReady && preview)) return
    const check = () => setOverflowing(node.scrollWidth > node.clientWidth + 2)
    check()
    const observer = new ResizeObserver(check)
    observer.observe(node)
    return () => observer.disconnect()
  }, [html, code, wrap, inline, isMermaid, visible, isHtml, previewReady, preview])

  const copy = async () => {
    if (timer.current) clearTimeout(timer.current)
    const ok = await copyText(code)
    setCopiedCode(ok ? code : null)
    if (ok) timer.current = setTimeout(() => setCopiedCode(null), 1600)
    else useAppStore.getState().toast({title: t('ui.components.chat.blocks.MessageActions.753d8bb0da99'),
      description: t('ui.components.chat.blocks.MessageActions.bc3be7231933'), tone: 'danger'})
  }
  const lines = useMemo(() => code.split('\n').length, [code])

  if (inline) {
    return <code className={className}>{code}</code>
  }

  if (isMermaid) {
    return <Mermaid chart={code} className={className} />
  }

  return (
    <figure className={cn('code-block group', className)}>
      <figcaption className="flex h-9 items-center gap-2 border-b border-line bg-raised/60 px-3">
        <span className="font-mono text-2xs font-medium uppercase tracking-wide text-faint">
          {fenceLanguage(language) || lang || t('ui.code.plainText')}
        </span>
        <span className="text-2xs text-faint/70">{t(lines === 1 ? "ui.filePreview.oneLine" : "ui.filePreview.lineCount", {count: lines})}</span>
        <div className="ml-auto flex items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100 pointer-coarse:opacity-100">
          {isHtml ? (
            <Tooltip content={preview ? t('ui.widget.viewCode') : t('ui.widget.viewWidget')}>
              <IconButton
                label={preview ? t('ui.widget.viewCode') : t('ui.widget.viewWidget')}
                size="xs"
                active={preview}
                disabled={!previewReady}
                onClick={() => setPreview((v) => !v)}
              >
                <Eye className="h-3.5 w-3.5" />
              </IconButton>
            </Tooltip>
          ) : null}
          <Tooltip content={wrap ? t("ui.components.markdown.CodeBlock.8180fd1d996c") : t("ui.components.markdown.CodeBlock.b096842c67b2")}>
            <IconButton
              label={wrap ? t("ui.components.markdown.CodeBlock.8180fd1d996c") : t("ui.components.markdown.CodeBlock.b096842c67b2")}
              size="xs"
              active={wrap}
              onClick={() => setWrap((v) => !v)}
            >
              <WrapText className="h-3.5 w-3.5" />
            </IconButton>
          </Tooltip>
          <Tooltip content={copied ? t("ui.components.markdown.CodeBlock.8f6f8d979c98") : t("ui.components.markdown.CodeBlock.420c20dd9fcc")}>
            <IconButton label={copied ? t("ui.components.markdown.CodeBlock.8f6f8d979c98") : t("ui.components.markdown.CodeBlock.420c20dd9fcc")} size="xs" onClick={copy}>
              {copied ? (
                <Check className="h-3.5 w-3.5 text-ok" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </IconButton>
          </Tooltip>
        </div>
      </figcaption>
      <div className="relative">
        {isHtml && previewReady ? <div hidden={!preview}><HtmlSandboxFrame code={code} title={t('ui.widget.htmlPreview')} active={preview} /></div> : null}
        <div hidden={isHtml && preview && previewReady}>
            <pre
              ref={preRef}
              className={cn(
                lang === 'json' && 'file-preview-source',
                wrap && 'whitespace-pre-wrap break-words',
              )}
            >
              {html ? (
                <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
              ) : (
                <code>{code}</code>
              )}
            </pre>
            {overflowing && !wrap ? (
              <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-raised to-transparent" />
            ) : null}
        </div>
      </div>
    </figure>
  )
})

