/** Run a private copy beside a node_modules link to the bundled Artifact Tool runtime.
 * Native package preservation keeps the Sunday template's audio, notes and relationships.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import JSZip from 'jszip';
import {Presentation, PresentationFile, FileBlob} from '@oai/artifact-tool';

const EMU=914400, W=12192000, H=6858000, ratio=4/3;
const box=(x,y,w,h)=>({xEmu:Math.round(x*EMU),yEmu:Math.round(y*EMU),widthEmu:Math.round(w*EMU),heightEmu:Math.round(h*EMU)});
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const textOf=x=>Array.isArray(x)?x.map(textOf).join(''):x&&typeof x==='object'?(typeof x.text==='string'?x.text:'')+Object.entries(x).filter(([k])=>k!=='text').map(([,v])=>textOf(v)).join(''):'';
const scaleWidths=(values,total)=>{const sum=values.reduce((a,b)=>a+b,0);const next=values.map(v=>Math.round(v*total/sum));next[next.length-1]+=total-next.reduce((a,b)=>a+b,0);return next;};
const specific={
  1:{18:box(.65,.35,12.03,.54),17:box(.65,.95,12.03,.44),3080:box(.65,1.5,12.03,.45),
     19:box(.65,2.0,12.03,1.15),12:box(.65,3.25,12.03,.35),3:box(.65,3.7,12.03,.4),
     22:box(.65,4.15,12.03,1.05),4:box(2.28,5.45,8.78,1.22),3075:box(.65,6.95,12.03,.3)},
  2:{2:box(.65,.35,12.03,.6),4:box(.65,1.1,12.03,5.1),3:box(8.68,6.85,4,.3)},
  5:{11266:box(1.62,.3,10.093,6.43)},
  7:{3:box(.65,.75,12.03,1.85),112:box(1.65,3.15,10.03,.7)},
  9:{2:box(.85,1.8,11.63,4.95)},
  11:{3:box(.65,.65,12.03,6.2)},
  12:{6:box(.65,.35,12.03,.6),4:box(.65,1.1,12.03,5.6)},
  13:{11:box(.65,.35,12.03,.5),2:box(.65,1.2,12.03,1.6),5:box(.65,3,12.03,.4),
      3:box(.65,3.55,12.03,.8),7:box(.65,4.6,12.03,2.48)},
  14:{2:box(.65,.35,12.03,.9),4:box(.65,1.75,12.03,4.9),3:box(.65,.4,1.5,.4)},
  15:{2:box(.65,.65,12.03,6.2)},
};

function planElement(element, slideNumber){
  const old=element.bbox?{...element.bbox}:null;
  const text=textOf(element.paragraphs||[]);
  let b=old?{...old,xEmu:Math.round(old.xEmu*ratio),widthEmu:Math.round(old.widthEmu*ratio)}:null;
  if(old&&element.type===7)b={...old,xEmu:Math.round((old.xEmu+old.widthEmu/2)*ratio-old.widthEmu/2)};
  if(old&&element.type===9)b.xEmu=Math.round((W-b.widthEmu)/2);
  let fontCap=null,footer=null;
  if(b&&text&&old.yEmu>6.5*EMU){
    const right=old.xEmu>6*EMU||text.includes('{reader}');
    b=right?box(8.68,6.85,4,.3):box(.65,6.85,7.4,.3);
    fontCap=right?20:18;footer=right?'r':'l';
  }
  if(['{scriptureVerses}','{hymnLyrics}','{responseHymnLyrics}'].some(token=>text.includes(token)))b=box(.65,1.55,12.03,5.1);
  if(specific[slideNumber]?.[element.id])b={...specific[slideNumber][element.id]};
  if(b&&text){b.yEmu=Math.max(Math.round(.3*EMU),Math.min(b.yEmu,H-Math.round(.3*EMU)-b.heightEmu));}
  if(slideNumber===2&&element.type===9)fontCap=21;
  if(slideNumber===1&&element.type===9)fontCap=20;
  if(slideNumber===1&&element.id==='22')fontCap=18;
  if(slideNumber===7&&element.type===9)fontCap=30;
  if(slideNumber===9&&element.id==='2')fontCap=60;
  if(slideNumber===14&&element.id==='4')fontCap=36;
  if(slideNumber===14&&element.id==='2')fontCap=48;
  if(slideNumber===5&&text.includes('主禱文'))fontCap=30;
  const centered=(slideNumber===11&&element.id==='3')||(slideNumber===15&&element.id==='2');
  const alignment=(slideNumber===1&&element.id==='3')||centered?'ctr':null;
  const noBorder=slideNumber===1&&element.id==='19';
  if(noBorder)element.shape.line.fill={type:0,gradientStops:[],pictureEffects:[]};
  if(alignment)for(const paragraph of element.paragraphs||[])paragraph.textStyle={...paragraph.textStyle,alignment:2};
  const doxology=slideNumber===14&&element.id==='4';
  if(doxology)for(const paragraph of element.paragraphs||[])paragraph.paragraphStyle={...paragraph.paragraphStyle,
    lineSpacingPoints:5400,spaceBeforePoints:0,spaceAfterPoints:0};
  if(b)element.bbox=b;
  if(element.table&&old&&b){
    element.table.columnWidths=scaleWidths(element.table.columnWidths,b.widthEmu);
    const heights=scaleWidths(element.table.rows.map(row=>row.heightEmu),b.heightEmu);
    element.table.rows.forEach((row,i)=>row.heightEmu=heights[i]);
  }
  if(fontCap){
    const cap=x=>{if(!x||typeof x!=='object')return;for(const[k,v]of Object.entries(x)){if(k==='fontSize'&&typeof v==='number')x[k]=Math.min(v,fontCap*100);else cap(v);}};
    cap(element);
  }
  return {id:element.id,bbox:b,oldBbox:old,fontCap,footer,alignment,centered,noBorder,table:element.type===9,
    doxology,sermon:slideNumber===9&&element.id==='2'};
}
function attrs(tag,values){
  for(const[k,v]of Object.entries(values)){
    const re=new RegExp(`\\b${k}="[^"]*"`);
    tag=re.test(tag)?tag.replace(re,`${k}="${v}"`):tag.replace(/\/?\>$/,m=>` ${k}="${v}"${m}`);
  }
  return tag;
}
function xfrm(block,b=null,preserve=false){
  const off=block.match(/<a:off\b[^>]*>/)?.[0],ext=block.match(/<a:ext\b[^>]*>/)?.[0];
  if(off&&ext){
    const number=(tag,key)=>Number(tag.match(new RegExp(`\\b${key}="(-?\\d+)"`))?.[1]||0);
    const x=number(off,'x'),cx=number(ext,'cx');
    const dest=b||{xEmu:preserve?Math.round((x+cx/2)*ratio-cx/2):Math.round(x*ratio),widthEmu:preserve?cx:Math.round(cx*ratio)};
    block=block.replace(off,attrs(off,{x:dest.xEmu,...(b?{y:b.yEmu}:{})})).replace(ext,attrs(ext,{cx:dest.widthEmu,...(b?{cy:b.heightEmu}:{})}));
  }
  if(!b)block=block.replace(/<a:chOff\b[^>]*>/g,t=>attrs(t,{x:Math.round(Number(t.match(/\bx="(-?\d+)"/)?.[1]||0)*ratio)}))
    .replace(/<a:chExt\b[^>]*>/g,t=>attrs(t,{cx:Math.round(Number(t.match(/\bcx="(\d+)"/)?.[1]||0)*ratio)}));
  return block;
}
function partGeometry(xml){
  const pictures=[];
  xml=xml.replace(/<p:pic\b[\s\S]*?<\/p:pic>/g,block=>{
    pictures.push(block.replace(/<(?:a|p):xfrm\b[\s\S]*?<\/(?:a|p):xfrm>/g,b=>xfrm(b,null,true)));
    return `__SUNDAY_PICTURE_${pictures.length-1}__`;
  });
  xml=xml.replace(/<(?:a|p):xfrm\b[\s\S]*?<\/(?:a|p):xfrm>/g,b=>xfrm(b));
  xml=xml.replace(/<a:tblGrid>[\s\S]*?<\/a:tblGrid>/g,grid=>{
    const values=[...grid.matchAll(/<a:gridCol\b[^>]*\bw="(\d+)"/g)].map(m=>Number(m[1]));
    const next=scaleWidths(values,Math.round(values.reduce((a,b)=>a+b,0)*ratio));let index=0;
    return grid.replace(/<a:gridCol\b[^>]*>/g,t=>attrs(t,{w:next[index++]}));
  });
  return xml.replace(/__SUNDAY_PICTURE_(\d+)__/g,(_,i)=>pictures[Number(i)]);
}
function applyElement(block,plan){
  const b=plan.bbox;if(!b)return block;
  if(plan.noBorder)block=block.replace(/<a:ln\b[^>]*>[\s\S]*?<\/a:ln>/,'<a:ln><a:noFill/></a:ln>');
  if(plan.centered)block=block.replace(/<a:bodyPr\b[^>]*>/,tag=>attrs(tag,{anchor:'ctr'}));
  const frame=block.match(/<(?:a|p):xfrm\b[\s\S]*?<\/(?:a|p):xfrm>/);
  if(frame)block=block.replace(frame[0],xfrm(frame[0],b));
  else if(block.startsWith('<p:sp')){
    const add=`<a:xfrm><a:off x="${b.xEmu}" y="${b.yEmu}"/><a:ext cx="${b.widthEmu}" cy="${b.heightEmu}"/></a:xfrm>`;
    block=block.includes('<p:spPr/>')?block.replace('<p:spPr/>',`<p:spPr>${add}</p:spPr>`):block.replace(/<p:spPr\b[^>]*>/,m=>m+add);
  }
  if(plan.table&&plan.oldBbox){
    const grid=block.match(/<a:tblGrid>[\s\S]*?<\/a:tblGrid>/)?.[0];
    if(grid){const values=[...grid.matchAll(/\bw="(\d+)"/g)].map(m=>Number(m[1]));const widths=scaleWidths(values,b.widthEmu);let i=0;block=block.replace(grid,grid.replace(/<a:gridCol\b[^>]*>/g,t=>attrs(t,{w:widths[i++]})));}
    const values=[...block.matchAll(/<a:tr\b[^>]*\bh="(\d+)"/g)].map(m=>Number(m[1]));
    const heights=scaleWidths(values,b.heightEmu);let i=0;
    block=block.replace(/<a:tr\b[^>]*>/g,t=>attrs(t,{h:heights[i++]}));
  }
  if(plan.fontCap){
    block=block.replace(/<a:(?:rPr|defRPr|endParaRPr)\b[^>]*>/g,t=>{
      const size=t.match(/\bsz="(\d+)"/);return attrs(t,{sz:Math.min(size?Number(size[1]):plan.fontCap*100,plan.fontCap*100)});
    });
    if(plan.table)block=block.replace(/<a:tcPr\b[^>]*>/g,t=>attrs(t,{marT:0,marB:0}));
  }
  if(plan.footer)block=block.replace(/<a:pPr\b[^>]*>/g,t=>attrs(t,{algn:plan.footer}));
  if(plan.alignment)block=block.replace(/<a:p>[\s\S]*?<\/a:p>/g,paragraph=>
    /<a:pPr\b/.test(paragraph)?paragraph.replace(/<a:pPr\b[^>]*>/,t=>attrs(t,{algn:plan.alignment}))
      :paragraph.replace('<a:p>',`<a:p><a:pPr algn="${plan.alignment}"/>`));
  if(plan.sermon)block=block.replace(/<a:p>[\s\S]*?<\/a:p>/g,paragraph=>{
    const cap=paragraph.includes('sermonSpeaker')?30:!paragraph.includes('<a:t>')?18:null;
    return cap?paragraph.replace(/<a:(?:rPr|defRPr|endParaRPr)\b[^>]*>/g,
      t=>attrs(t,{sz:Math.min(Number(t.match(/\bsz="(\d+)"/)?.[1]||cap*100),cap*100)})):paragraph;
  });
  if(plan.doxology){
    block=block.replace(/<a:bodyPr\b[^>]*>/,tag=>attrs(tag,{anchor:'ctr'}));
    block=block.replace(/<a:p>[\s\S]*?<\/a:p>/g,paragraph=>{
      const spacing='<a:lnSpc><a:spcPts val="5400"/></a:lnSpc><a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft>';
      const existing=paragraph.match(/<a:pPr\b[^>]*(?:\/>|>[\s\S]*?<\/a:pPr>)/)?.[0];
      let properties=(existing||'<a:pPr algn="ctr"/>').replace(/<a:(?:lnSpc|spcBef|spcAft)>[\s\S]*?<\/a:(?:lnSpc|spcBef|spcAft)>/g,'');
      properties=properties.endsWith('/>')?properties.slice(0,-2)+'>'+spacing+'</a:pPr>':properties.replace('</a:pPr>',spacing+'</a:pPr>');
      return existing?paragraph.replace(existing,properties):paragraph.replace('<a:p>','<a:p>'+properties);
    });
  }
  return block;
}

async function main(){
  const [input,output,expected]=process.argv.slice(2);
  if(!input||!output||!expected)throw new Error('usage: builder input.pptx candidate.pptx expected-source-sha256');
  const source=await fs.readFile(input);
  if(digest(source)!==expected)throw new Error('Source template changed; refuse an unreviewed migration.');
  const presentation=await PresentationFile.importPptx(await FileBlob.load(input));
  const proto=presentation.toProto();
  if(proto.slides.length!==16||proto.slides[0].widthEmu!==9144000||proto.slides[0].heightEmu!==H)throw new Error('Expected the reviewed 16-slide 4:3 Sunday template.');
  const plans={};
  for(const[index,slide]of proto.slides.entries()){
    slide.widthEmu=W;slide.heightEmu=H;
    plans[index+1]=slide.elements.map(e=>planElement(e,index+1));
  }
  for(const layout of proto.layouts||[]){for(const element of layout.elements||[])planElement(element,0);}
  const edited=Presentation.load(proto);
  await fs.writeFile(path.join(path.dirname(output),'artifact-layout-plan.json'),JSON.stringify(edited.toProto(),null,2));
  // Export the Artifact-authored draft for comparison; preserve the native source
  // package for the final candidate because the template contains embedded audio.
  await (await PresentationFile.exportPptx(edited)).save(path.join(path.dirname(output),'artifact-draft.pptx'));
  const zip=await JSZip.loadAsync(source);const changed=[];
  for(const name of Object.keys(zip.files)){
    if(!/^ppt\/(?:slides\/slide\d+|slideLayouts\/slideLayout\d+|slideMasters\/slideMaster\d+)\.xml$/.test(name))continue;
    let xml=await zip.file(name).async('string');const before=xml;
    xml=partGeometry(xml);
    const number=name.match(/^ppt\/slides\/slide(\d+)\.xml$/)?.[1];
    if(number){const map=new Map(plans[Number(number)].map(p=>[String(p.id),p]));
      // The cover title is a native group. Its children now use slide coordinates,
      // so its transform must be identity rather than the old local coordinate map.
      if(Number(number)===1)xml=xml.replace(/<p:grpSpPr\b[\s\S]*?<\/p:grpSpPr>/g,
        block=>block.replace(/<a:xfrm\b[\s\S]*?<\/a:xfrm>/,
          `<a:xfrm><a:off x="0" y="0"/><a:ext cx="${W}" cy="${H}"/><a:chOff x="0" y="0"/><a:chExt cx="${W}" cy="${H}"/></a:xfrm>`));
      xml=xml.replace(/<p:(?:sp|pic|graphicFrame)\b[\s\S]*?<\/p:(?:sp|pic|graphicFrame)>/g,block=>{
        const id=block.match(/<p:cNvPr\b[^>]*\bid="(\d+)"/)?.[1];return map.has(id)?applyElement(block,map.get(id)):block;
      });
    }
    if(xml!==before){zip.file(name,xml);changed.push(name);}
  }
  let presentationXml=await zip.file('ppt/presentation.xml').async('string');
  presentationXml=presentationXml.replace(/<p:sldSz\b[^>]*>/,tag=>attrs(tag,{cx:W,cy:H,type:'screen16x9'}));
  zip.file('ppt/presentation.xml',presentationXml);
  const bytes=await zip.generateAsync({type:'nodebuffer',compression:'DEFLATE'});
  await fs.writeFile(output,bytes,{flag:'wx'});
  await fs.writeFile(path.join(path.dirname(output),'migration.json'),JSON.stringify({sourceSha256:expected,candidateSha256:digest(bytes),slideCount:16,sizeEmu:[W,H],changedParts:changed,plans},null,2));
  console.log(JSON.stringify({output,sourceSha256:expected,candidateSha256:digest(bytes),slideCount:16}));
}
main().catch(error=>{console.error(String(error));process.exitCode=1;});
