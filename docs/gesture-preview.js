"use strict";
const manifest = window.MORI_GESTURES;
const images = new Map(), cards = [];
const playButton = document.querySelector("#play"), status = document.querySelector("#status");
let playing = !matchMedia("(prefers-reduced-motion: reduce)").matches, speed = 1, last = 0;
const specs = [
  {id:"cheek-left", title:"戳左脸", view:"眨眼 → 轻捂脸", description:"戳到左侧脸颊，偏头眨眼，再轻轻捂住这一侧。", reaction:true},
  {id:"cheek-right", title:"戳右脸", view:"独立右侧反应", description:"右侧脸颊也会害羞；尾巴始终留在原来的一侧。", reaction:true},
  {id:"pickup", title:"被轻轻提起来", view:"悬空 · 方向惯性", description:"四张姿势展示悬空、左右滞后和向上提起；实际拖动在上方试验台体验。"},
  {id:"landing", title:"稳稳落地", view:"缓冲 → 整理裙摆", description:"屈膝接住重量，慢慢站好，轻拍裙摆，再回到安静陪伴。", replay:true}
];
function syncPlay(){playButton.textContent=playing?"暂停":"播放";playButton.setAttribute("aria-pressed",String(playing));}
syncPlay(); if(!playing)status.textContent="已按系统减少动态效果设置暂停，可手动播放或逐帧查看。";
function frameIndex(clip,elapsed){const total=clip.durations.reduce((a,b)=>a+b,0);let ms=clip.loop?elapsed%total:Math.min(elapsed,total-1);for(let i=0;i<clip.durations.length;i++){if(ms<clip.durations[i])return i;ms-=clip.durations[i];}return clip.frames.length-1;}
function drawCard(card){const index=frameIndex(card.clip,card.elapsed),image=images.get(card.clip.frames[index]);if(!image)return;card.ctx.clearRect(0,0,280,230);card.ctx.drawImage(image,0,0);card.pose.textContent=`关键姿势 ${Number(card.clip.frames[index].match(/-(\d+)\.png$/)[1])+1} / 4`;}
function react(card,forced){const now=performance.now();card.level=forced||((now-card.lastTouch<12000)?Math.min(3,card.level+1):1);card.lastTouch=now;card.variant=1-card.variant;card.clip=manifest.clips[`${card.spec.id}-${card.level}-${card.variant}`];card.elapsed=0;card.state.textContent=["轻轻回应","更亲近一点","害羞地回应"][card.level-1]+` · 变化 ${card.variant+1}`;drawCard(card);}
for(const spec of specs){
  const section=document.createElement("section");section.className="card";
  section.innerHTML=`<div class="card-head"><h2>${spec.title}</h2><span class="view">${spec.view}</span></div><div class="stage"><div class="ground"></div><canvas width="280" height="230" role="img" aria-label="${spec.title}动画"></canvas></div><div class="details"><p class="description">${spec.description}</p><span class="pose"></span>${spec.reaction?`<div class="reaction-controls"><button>连续戳脸</button><select aria-label="${spec.title}反应层级"><option value="1">轻度</option><option value="2">亲近</option><option value="3">害羞</option></select><button class="replay">重播所选</button></div><div class="reaction-state" aria-live="polite">等待你的互动</div>`:spec.replay?`<div class="reaction-controls"><button>重播落地</button></div>`:""}</div>`;
  document.querySelector("#cards").append(section);
  const clip=manifest.clips[spec.reaction?`${spec.id}-1-0`:spec.id];
  const card={spec,section,clip,ctx:section.querySelector("canvas").getContext("2d"),pose:section.querySelector(".pose"),elapsed:clip.loop?0:clip.durations.reduce((a,b)=>a+b,0),level:0,variant:1,lastTouch:-Infinity,state:section.querySelector(".reaction-state")};
  if(spec.reaction){section.querySelector("button").onclick=()=>react(card);section.querySelector(".replay").onclick=()=>react(card,Number(section.querySelector("select").value));}
  if(spec.replay)section.querySelector("button").onclick=()=>{card.elapsed=0;drawCard(card);};
  cards.push(card);
}

// Same bounded spring parameters and small integration steps as DragDynamics.
function newDynamics(){return {vx:0,vy:0,angle:0,angular:0,stretch:1};}
function pushMotion(motion,dx,dy,seconds){const dt=Math.max(1/120,Math.min(.25,seconds));motion.vx=Math.max(-1800,Math.min(1800,dx/dt));motion.vy=Math.max(-1800,Math.min(1800,dy/dt));}
function advanceMotion(motion,seconds){let remaining=Math.max(0,Math.min(.08,seconds));while(remaining>1e-8){const dt=Math.min(1/120,remaining),target=Math.max(-7,Math.min(7,motion.vx*.012));motion.angular+=(110*(target-motion.angle)-18*motion.angular)*dt;motion.angle=Math.max(-9,Math.min(9,motion.angle+motion.angular*dt));const stretch=1-Math.max(-.035,Math.min(.035,motion.vy*.00004));motion.stretch+=(stretch-motion.stretch)*(1-Math.exp(-12*dt));const decay=Math.exp(-dt/.12);motion.vx*=decay;motion.vy*=decay;remaining-=dt;}}
function motionPose(m){return Math.max(Math.abs(m.vx),Math.abs(m.vy))<45?0:Math.abs(m.vy)>Math.abs(m.vx)*1.2?3:m.vx>0?1:2;}
const arena=document.querySelector("#arena"),arenaCtx=arena.getContext("2d"),arenaState=document.querySelector("#arena-state");
const actor={x:140,y:75,pivot:{x:185,y:65},motion:newDynamics(),mode:"idle",elapsed:0,pointer:null,lastPoint:null,lastSample:0,offset:null};
function setState(text){arenaState.textContent=text;}
function startGrab(point){actor.mode="pickup";actor.elapsed=0;actor.motion=newDynamics();actor.pivot={x:point.x-actor.x,y:point.y-actor.y};actor.offset={...actor.pivot};actor.lastPoint=point;actor.lastSample=performance.now();setState("悬空啦，身体和尾巴会慢一点跟上");}
function release(){if(actor.mode!=="pickup")return;actor.mode="landing";actor.elapsed=0;actor.motion=newDynamics();setState("屈膝缓冲，整理一下裙摆～");}
function actorImage(){const clip=manifest.clips[actor.mode==="pickup"?"pickup":"landing"];const index=actor.mode==="pickup"?(actor.elapsed<160?0:motionPose(actor.motion)):actor.mode==="idle"?3:frameIndex(clip,actor.elapsed);return images.get(clip.frames[index]);}
function fitMotion(image){let angle=actor.motion.angle,stretch=actor.motion.stretch;const b=image.bounds,p=actor.pivot;for(let i=0;i<10;i++){const radians=angle*Math.PI/180,c=Math.cos(radians),s=Math.sin(radians);const corners=[[b.left,b.top],[b.right,b.top],[b.left,b.bottom],[b.right,b.bottom]].map(([x,y])=>{const dx=x-p.x,dy=(y-p.y)*stretch;return {x:p.x+dx*c-dy*s,y:p.y+dx*s+dy*c};});if(corners.every(q=>q.x>=1&&q.x<=279&&q.y>=1&&q.y<=229))return {angle,stretch};angle*=.7;stretch=1+(stretch-1)*.7;}return {angle:0,stretch:1};}
function drawArena(){const image=actorImage();if(!image)return;arenaCtx.clearRect(0,0,560,340);arenaCtx.strokeStyle="#425a72";arenaCtx.beginPath();arenaCtx.moveTo(actor.x+16,actor.y+manifest.baseline);arenaCtx.lineTo(actor.x+264,actor.y+manifest.baseline);arenaCtx.stroke();arenaCtx.save();arenaCtx.translate(actor.x,actor.y);if(actor.mode==="pickup"){const fitted=fitMotion(image),p=actor.pivot;arenaCtx.translate(p.x,p.y);arenaCtx.rotate(fitted.angle*Math.PI/180);arenaCtx.scale(1,fitted.stretch);arenaCtx.translate(-p.x,-p.y);}arenaCtx.drawImage(image,0,0);arenaCtx.restore();}
function arenaPoint(event){const r=arena.getBoundingClientRect();return {x:(event.clientX-r.left)*560/r.width,y:(event.clientY-r.top)*340/r.height};}
arena.onpointerdown=event=>{if(actor.pointer!==null)return;const point=arenaPoint(event),image=actorImage();if(!image)return;const b=image.bounds;if(point.x<actor.x+b.left||point.x>actor.x+b.right||point.y<actor.y+b.top||point.y>actor.y+b.bottom)return;event.preventDefault();actor.pointer=event.pointerId;arena.setPointerCapture(event.pointerId);startGrab(point);drawArena();};
arena.onpointermove=event=>{if(event.pointerId!==actor.pointer)return;const point=arenaPoint(event),now=performance.now();pushMotion(actor.motion,point.x-actor.lastPoint.x,point.y-actor.lastPoint.y,(now-actor.lastSample)/1000);actor.x=Math.max(0,Math.min(280,point.x-actor.offset.x));actor.y=Math.max(0,Math.min(110,point.y-actor.offset.y));actor.lastPoint=point;actor.lastSample=now;drawArena();};
function endPointer(event){if(event.pointerId!==actor.pointer)return;const id=actor.pointer;actor.pointer=null;if(arena.hasPointerCapture(id))arena.releasePointerCapture(id);release();drawArena();}
arena.onpointerup=endPointer;arena.onpointercancel=endPointer;arena.onlostpointercapture=event=>{if(event.pointerId===actor.pointer){actor.pointer=null;release();}};
for(const button of document.querySelectorAll("[data-drag]"))button.onclick=()=>{if(actor.mode!=="pickup")startGrab({x:actor.x+185,y:actor.y+65});actor.elapsed=180;const direction=button.dataset.drag,dx=direction==="left"?-24:direction==="right"?24:0,dy=direction==="up"?-18:direction==="down"?18:0;pushMotion(actor.motion,dx,dy,.04);actor.x=Math.max(0,Math.min(280,actor.x+dx));actor.y=Math.max(0,Math.min(110,actor.y+dy));drawArena();};
document.querySelector("#release").onclick=()=>{release();drawArena();};
document.querySelector("#reset").onclick=()=>{if(actor.pointer!==null&&arena.hasPointerCapture(actor.pointer))arena.releasePointerCapture(actor.pointer);actor.pointer=null;actor.mode="idle";actor.x=140;actor.y=75;actor.elapsed=0;actor.motion=newDynamics();setState("站好啦，等你来玩");drawArena();};
function advanceArena(ms){actor.elapsed+=ms;if(actor.mode==="pickup")advanceMotion(actor.motion,ms/1000);if(actor.mode==="landing"&&actor.elapsed>=manifest.clips.landing.durations.reduce((a,b)=>a+b,0)){actor.mode="idle";setState("站稳啦，回到你松手的位置");}drawArena();}
playButton.onclick=()=>{playing=!playing;syncPlay();status.textContent="";};
document.querySelector("#speed").onchange=event=>{speed=Number(event.target.value);};
document.querySelector("#step").onclick=()=>{playing=false;syncPlay();for(const card of cards){const index=frameIndex(card.clip,card.elapsed),total=card.clip.durations.reduce((a,b)=>a+b,0);const next=card.clip.durations.slice(0,index+1).reduce((a,b)=>a+b,0);card.elapsed=next>=total?(card.clip.loop?0:total-1):next;drawCard(card);}advanceArena(40);};
document.querySelector("#background").onclick=event=>{const enabled=event.currentTarget.getAttribute("aria-pressed")!=="true";event.currentTarget.setAttribute("aria-pressed",String(enabled));document.querySelectorAll(".stage").forEach(stage=>stage.classList.toggle("checker",enabled));};
document.addEventListener("visibilitychange",()=>{last=0;if(document.hidden&&actor.pointer!==null){actor.pointer=null;release();}});
async function loadImage(file){const image=new Image();image.src="../src/asset_library/motion/"+file;await image.decode();image.bounds=manifest.bounds[file];images.set(file,image);}
function tick(now){const delta=last?Math.min(80,now-last):0;last=now;if(playing&&!document.hidden){const ms=delta*speed;for(const card of cards){card.elapsed+=ms;drawCard(card);}advanceArena(ms);}requestAnimationFrame(tick);}
Promise.all([...new Set(Object.values(manifest.clips).flatMap(clip=>clip.frames))].map(loadImage)).then(()=>{cards.forEach(drawCard);drawArena();requestAnimationFrame(tick);}).catch(error=>{status.textContent="素材读取失败，请从项目目录打开此预览。";console.error(error);});
