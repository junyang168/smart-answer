const audio = document.querySelector('audio');
let token=0, status='stopped', stopping=false;
function report() {
  window.musicBridge.report({token,status,position:audio.currentTime||0,duration:Number.isFinite(audio.duration)?audio.duration:0});
}
for (const event of ['loadedmetadata','durationchange','timeupdate','seeked']) audio.addEventListener(event,report);
audio.addEventListener('canplay',()=>{if(status==='loading'){status='stopped';report();}});
audio.addEventListener('playing',()=>{status='playing';report();});
audio.addEventListener('pause',()=>{if(!stopping&&status==='playing'){status='paused';report();}});
audio.addEventListener('ended',()=>{status='ended';report();});
audio.addEventListener('error',()=>{status='error';report();});
window.musicPlayer={
  async command(action,args){
    if(action==='load'){
      stopping=true;audio.pause();token=args.token;status='loading';audio.src=args.url;audio.volume=args.volume;audio.load();stopping=false;report();return;
    }
    if(args.token!==token)throw Error('音樂項目已切換。');
    switch(action){
      case 'play': { const playingToken=token;if(audio.ended)audio.currentTime=0;try{await audio.play();}catch(error){if(playingToken!==token||['paused','stopped'].includes(status)&&error.name==='AbortError')return;throw error;}break; }
      case 'pause':status='paused';audio.pause();break;
      case 'stop':stopping=true;status='stopped';audio.pause();if(audio.readyState)audio.currentTime=0;stopping=false;break;
      case 'seek':audio.currentTime=Math.max(0,Math.min(audio.duration||0,args.position));break;
      case 'volume':audio.volume=args.volume;break;
      default:throw Error('未知音樂操作。');
    }
    report();
  }
};
