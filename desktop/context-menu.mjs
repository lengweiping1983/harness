import {websiteURL} from './browser-policy.mjs'

export function websiteMenu(params, actions, text) {
  const items = []
  let link
  try {link = websiteURL(params.linkURL)} catch {}
  if (link) {
    items.push({id:'open-link', label:text('contextOpenLink'), click:() => actions.open(link)})
    items.push({id:'copy-link', label:text('contextCopyLink'), click:() => actions.copyLink(link)})
    items.push({type:'separator'})
  }
  if (params.isEditable) {
    for (const [id, flag] of [['undo','canUndo'], ['redo','canRedo'], ['cut','canCut'], ['copy','canCopy'], ['paste','canPaste']]) {
      items.push({id, label:text(id), enabled:!!params.editFlags?.[flag], click:() => actions.edit(id)})
    }
    items.push({id:'selectAll', label:text('selectAll'), click:() => actions.edit('selectAll')})
  } else if (params.selectionText && params.editFlags?.canCopy) {
    items.push({id:'copy', label:text('copy'), click:() => actions.edit('copy')})
  }
  if (items.at(-1)?.type === 'separator') items.pop()
  if (actions.inspect) {
    if (items.length) items.push({type:'separator'})
    items.push({id:'inspect', label:text('contextInspect'), click:() => actions.inspect(params.x, params.y)})
  }
  return items
}
