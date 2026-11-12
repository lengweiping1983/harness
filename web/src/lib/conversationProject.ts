import type {ConversationProject} from './protocol.generated'

/** 草稿中的检出选择绑定原目录；登记项目后再补充注册身份。 */
export type ConversationCheckout = Omit<ConversationProject, 'workspace_id'> & {cwd: string}
