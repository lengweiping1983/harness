const CHANNEL = 'harness-widget'
export const SANDBOX_INITIAL_HEIGHT = 480

/** 在模型脚本之前安装：握手、主题、布局与错误均绑定单次文档身份。 */
export function sandboxBootstrap(identity: string): string {
  return `(()=>{
    const id=${JSON.stringify(identity)},channel=${JSON.stringify(CHANNEL)};
    const post=parent.postMessage.bind(parent),raf=requestAnimationFrame.bind(window),Observer=ResizeObserver;
    const send=(kind,data={})=>post({channel,id,kind,...data},'*');
    let lastFailure=null,lastCopy=null,queued=false,pendingPaint=null,trapFocus=false,active=false,lastHeight=null,expanded=false,viewportLayout=false,measurement=null;
    const observer=new Observer(resize);
    const notify=()=>{
      if(!active||expanded||document.readyState==='loading'||!document.body)return;
      const content=Math.max(document.body.scrollHeight,document.body.getBoundingClientRect().height);
      const viewport=window.innerHeight,width=window.innerWidth;
      // 识别内容随宿主测高反过来变化的反馈，避免 vh／百分比布局不断缩小或增长。
      if(measurement&&viewport===lastHeight&&width===measurement.width&&(viewport-measurement.viewport)*(content-measurement.content)>0)viewportLayout=true;
      if(!viewportLayout&&content<viewport)viewportLayout=[...document.body.querySelectorAll('*')].some(node=>{
        const position=getComputedStyle(node).position;
        if(position!=='fixed'&&position!=='absolute')return false;
        const rect=node.getBoundingClientRect();
        return rect.width>0&&rect.height>0&&(position==='fixed'||rect.bottom>=viewport-1);
      });
      measurement={viewport,width,content};
      const height=Math.min(2000,Math.max(40,Math.ceil(viewportLayout?${SANDBOX_INITIAL_HEIGHT}:content)));
      if(height!==lastHeight){lastHeight=height;send('resize',{height,paint:pendingPaint})}
    };
    const paintReady=ticket=>{const paint=()=>raf(()=>raf(()=>{if(active&&pendingPaint===ticket&&document.readyState!=='loading')send('ready',{paint:ticket})}));const content=window.__harnessWidgetReady;if(content&&typeof content.then==='function')content.then(paint,error=>fail(String(error?.message||error),error?.harnessCopy));else paint()};
    function resize(){if(!active||queued)return;queued=true;raf(()=>{queued=false;notify()})};
    const fail=(message,copy=null)=>{if(message===lastFailure&&copy===lastCopy)return;lastFailure=message;lastCopy=typeof copy==='string'?copy:null;send('error',{message,copy:lastCopy})};
    addEventListener('error',e=>fail(e.message||e.target?.src||'',e.error?.harnessCopy),true);
    addEventListener('unhandledrejection',e=>fail(String(e.reason?.message||e.reason||''),e.reason?.harnessCopy));
    addEventListener('message',e=>{
      const data=e.data;
      if(e.source!==parent||data?.channel!==channel||data?.id!==id||data?.kind!=='theme'||typeof data.css!=='string')return;
      active=data.active===true;
      const paint=typeof data.paint==='string'?data.paint:null;
      if(expanded!==(data.expanded===true)||pendingPaint!==paint){measurement=null;lastHeight=null}
      expanded=data.expanded===true;
      pendingPaint=paint;
      observer.disconnect();
      if(active&&document.body)observer.observe(document.body);
      document.documentElement.classList.toggle('dark',!!data.dark);
      for(const value of data.css.split(';')){const at=value.indexOf(':');if(at>0)document.documentElement.style.setProperty(value.slice(0,at),value.slice(at+1))}
      document.documentElement.style.colorScheme=data.dark?'dark':'light';
      notify();
      trapFocus=!!data.trapFocus;
      if(pendingPaint&&document.readyState!=='loading')paintReady(pendingPaint);
      if(lastFailure!==null)send('error',{message:lastFailure,copy:lastCopy});
    });
    addEventListener('DOMContentLoaded',()=>{if(active)observer.observe(document.body);notify();if(pendingPaint)paintReady(pendingPaint)});
    addEventListener('load',notify);
    addEventListener('resize',resize);
    addEventListener('keydown',event=>{
      if(!active||!event.isTrusted||event.defaultPrevented||event.isComposing||event.keyCode===229)return;
      if(event.key==='Escape')send('escape');
      else if(event.key==='Tab'&&trapFocus){
        const nodes=[...document.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')].filter(node=>!node.disabled&&node.tabIndex>=0&&node.getClientRects().length);
        const boundary=event.shiftKey?nodes[0]:nodes.at(-1);
        if(!nodes.length||document.activeElement===boundary){event.preventDefault();send('focus-boundary',{backward:event.shiftKey})}
      }
    });
    document.fonts?.ready.then(resize).catch(()=>{});
    send('boot');
  })();`
}
