/** JSON 可以转义孤立代理码点；展示／下载前明确拒绝，不能隐式改写为替换字符。 */
export function hasUnpairedSurrogate(value:string):boolean{
 return /[\uD800-\uDFFF]/.test(value.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g,''))
}
