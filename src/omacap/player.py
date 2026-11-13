"""A play-along page: the recording and its chords, bar by bar, in one HTML file.

The page plays the recording itself, so the chords can be followed while the
music runs. It is a single file with no network access - the audio is referenced
by name, which is why it is written beside the recording - and it is built from
the same bars, beats and phrases the chord chart uses, so the two never disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

from .analysis.chords import NO_CHORD
from .analysis.structure import find_phrases
from .chordgrid import _runs, bar_source, beat_labels

#: Shown on every beat a chord is held after the one it starts on.
HOLD = "·"


def bar_beats(bar, spans, meter) -> list[str]:
    """Which chord holds each beat, the way the chart reads the bar."""
    if not [c for c in bar.chords if c != NO_CHORD]:
        return [NO_CHORD] * max(1, meter.beats_per_bar)
    return [label for label, count in _runs(beat_labels(bar, spans, meter), bar.chords)
            for _ in range(count)]


def beats_text(bar, spans, meter) -> str:
    """The bar beat by beat, ``D · · G``; a chord held all bar is written alone."""
    if not [c for c in bar.chords if c != NO_CHORD]:
        return NO_CHORD
    runs = _runs(beat_labels(bar, spans, meter), bar.chords)
    if len({label for label, _ in runs}) == 1:
        return runs[0][0]
    return " ".join(" ".join([label] + [HOLD] * (count - 1)) for label, count in runs)


def notes_text(bar, spans, meter) -> str:
    """The bar as the chordgrid chart writes it, ``D[2.] G[4]``."""
    if not [c for c in bar.chords if c != NO_CHORD]:
        return NO_CHORD   # the chart writes rests here; on this page a name reads better
    return bar_source(bar, spans, meter)


def player_data(analysis, audio_name: str, edit: str | None = None,
                notes: list[str] | None = None) -> dict:
    """Everything the page needs, as plain JSON.

    ``edit`` is the token a served page posts corrections with; a page written
    to disk has none and is read-only. ``notes`` is anything the page should say
    about the chart - that it was left alone because of a hand edit, say.
    """
    unsure = analysis.uncertain_bars
    letters: dict[int, str] = {}
    sections = []
    for phrase in find_phrases(analysis.bars):
        if not phrase.letter:
            continue
        first, last = phrase.start, phrase.start + phrase.total_bars - 1
        sections.append({"letter": phrase.letter, "first": first, "last": last,
                         "repeats": phrase.repeats})
        letters.update({n: phrase.letter for n in range(first, last + 1)})
    return {
        "title": analysis.source.stem,
        "audio": audio_name,
        "audio_start": analysis.audio_start,
        "key": analysis.key.name,
        "tempo": round(analysis.tempo),
        "metre": analysis.meter.name,
        "bars": [{
            "n": bar.number,
            "start": round(bar.start, 3),
            "end": round(bar.end, 3),
            "beats": beats_text(bar, analysis.chords, analysis.meter),
            "notes": notes_text(bar, analysis.chords, analysis.meter),
            "grid": bar_beats(bar, analysis.chords, analysis.meter),
            "section": letters.get(bar.number, ""),
            "unsure": bar.number in unsure,
            "corrected": getattr(bar, "corrected", False),
        } for bar in analysis.bars],
        "sections": sections,
        "edit": edit,
        "notes": list(notes or []),
    }


def render_player(analysis, audio_name: str, edit: str | None = None,
                  notes: list[str] | None = None) -> str:
    data = json.dumps(player_data(analysis, audio_name, edit, notes), ensure_ascii=False)
    # "</script>" inside a title or file name would end the script early.
    return TEMPLATE.replace("__DATA__", data.replace("</", "<\\/"))


def default_player_path(audio_path: Path) -> Path:
    return Path(audio_path).with_suffix(".html")


def write_player(analysis, audio_path: Path) -> Path:
    """Write the page beside the recording, which it refers to by name."""
    audio_path = Path(audio_path)
    target = default_player_path(audio_path)
    target.write_text(render_player(analysis, audio_path.name), encoding="utf-8")
    return target


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Play along</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#d9d5cc;--card:#fff;--beat:#ecebe6;--acc:#2d5bd7;--on:#cfdcff;--nc:#9b9a96;--loop:#fff4d6;--err:#c0392b}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#171716;--fg:#ecebe7;--mut:#9c9a94;--line:#3a3936;--card:#1f1f1d;--beat:#2c2c29;--acc:#8aa8ff;--on:#2f3d66;--nc:#77756f;--loop:#3a3220;--err:#ff8a7a}}
:root[data-theme="dark"]{--bg:#171716;--fg:#ecebe7;--mut:#9c9a94;--line:#3a3936;--card:#1f1f1d;--beat:#2c2c29;--acc:#8aa8ff;--on:#2f3d66;--nc:#77756f;--loop:#3a3220;--err:#ff8a7a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px 16px 60px}h1{font-size:22px;margin:0}.mut{color:var(--mut)}
.top{position:sticky;top:0;z-index:2;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line)}
audio{width:100%}.ctl{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin-top:8px;font-size:14px}
select,input[type=number],button{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
input[type=number]{width:4.5em}button[aria-pressed=true]{background:var(--fg);color:var(--bg)}
#err{color:var(--err);min-height:1.2em;font-size:14px}#notes{color:var(--err);font-size:14px}#notes:empty{display:none}
.line{display:flex;gap:8px;margin-top:10px}.lab{width:1.6em;flex:none;font-weight:600;color:var(--mut);padding-top:20px;text-align:center}
.bars{flex:1;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}
@media (max-width:640px){.bars{grid-template-columns:repeat(2,minmax(0,1fr))}}
.b{border-left:3px solid var(--line);padding:2px 4px 4px 6px;border-radius:2px;cursor:pointer;min-width:0}
.b.loop{background:var(--loop)}.b.now{border-left-color:var(--acc)}
.hd{display:flex;justify-content:space-between;font-size:11px;color:var(--mut);height:16px}
.hd .fx{color:var(--acc)}
.beats{display:flex;gap:3px}
.beat{flex:1;min-width:0;background:var(--beat);border-radius:4px;height:40px;display:flex;align-items:center;padding:0 5px;
 font:600 16px ui-monospace,monospace;white-space:nowrap;overflow:visible}
.beat.on{background:var(--on)}.nc{color:var(--nc);font-weight:400}
.txt{background:var(--beat);border-radius:4px;height:40px;display:flex;align-items:center;padding:0 8px;font:600 16px ui-monospace,monospace;overflow:hidden;white-space:nowrap}
.b.now .txt{background:var(--on)}
.b input{width:100%;height:40px;font:600 15px ui-monospace,monospace;color:var(--fg);background:var(--card);border:2px solid var(--acc);border-radius:4px;padding:0 6px}
code{font-size:13px}
</style></head><body><main>
<h1 id="title"></h1><div class="mut" id="facts"></div>
<div class="top"><audio id="au" controls preload="auto"></audio>
<div class="ctl">
 <span>Show <button id="nb" data-v="beats">beats</button><button id="nn" data-v="notes">note values</button></span>
 <label>Speed <select id="speed"><option>100</option><option>90</option><option>80</option><option>75</option><option>70</option><option>60</option><option>50</option></select>%</label>
 <label><input type="checkbox" id="follow" checked> follow</label>
 <label>Loop <select id="loop"><option value="">off</option></select></label>
 <span id="range" hidden>bars <input type="number" id="from" min="1"> to <input type="number" id="to" min="1"></span>
 <label id="samewrap" hidden><input type="checkbox" id="same"> correct every bar written the same</label>
</div><div id="notes"></div><div id="err"></div></div>
<div id="grid"></div>
<p class="mut">Click a beat to play from it. <b>?</b> marks a bar the analysis is less sure of. Chords are an estimate.</p>
<p class="mut" id="edithelp" hidden>Double-click a bar to correct it, then press Enter. Write <code>C</code>, <code>C / G</code>,
<code>D · · G</code> (a chord or · per beat), <code>D[2.] G[4]</code> or <code>%</code> for the bar before.
Leave it empty to go back to what omacap heard. <b>✎</b> marks a bar you corrected; it is saved beside the
recording and in the chart, and kept when the song is analysed again.</p>
</main>
<script>
let D=__DATA__;const au=document.getElementById('au');const $=id=>document.getElementById(id);
const store={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=t=>Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0');
const NC='N.C.',PER_LINE=4;
document.title=D.title+' - play along';$('title').textContent=D.title;
$('facts').textContent=[D.key,D.tempo+' BPM',D.metre,D.bars.length+' bars'].join(' · ');
au.src=encodeURIComponent(D.audio);au.preservesPitch=true;
let view=store.get('view')==='notes'?'notes':'beats',els=[],cur=-1,curBeat=-1,loop=null;
function barHTML(b,i){
 const mark=b.corrected?'<span class="fx">✎</span>':b.unsure?'<b>?</b>':'';
 let body;
 if(view==='notes')body=`<div class="txt${b.notes===NC?' nc':''}">${esc(b.notes)}</div>`;
 else body='<div class="beats">'+b.grid.map((c,j)=>{const show=j===0||c!==b.grid[j-1];
  return `<div class="beat${c===NC?' nc':''}" data-j="${j}">${show?esc(c):''}</div>`}).join('')+'</div>';
 return `<div class="b" data-i="${i}" title="${fmt(b.start)}"><div class="hd"><span>${b.n}</span>${mark}</div>${body}</div>`}
function draw(){
 $('notes').innerHTML=(D.notes||[]).map(n=>'⚠ The chart: '+esc(n)).join('<br>');
 $('nb').setAttribute('aria-pressed',view==='beats');$('nn').setAttribute('aria-pressed',view==='notes');
 const firsts=new Set(D.sections.map(s=>s.first)),lasts=new Set(D.sections.map(s=>s.last));
 const lines=[];let line=null;
 D.bars.forEach((b,i)=>{if(!line||line.bars.length===PER_LINE||firsts.has(b.n)||lasts.has(b.n-1)){line={label:firsts.has(b.n)?b.section:'',bars:[]};lines.push(line)}line.bars.push(i)});
 $('grid').innerHTML=lines.map(l=>`<div class="line"><div class="lab">${esc(l.label)}</div><div class="bars">${l.bars.map(i=>barHTML(D.bars[i],i)).join('')}</div></div>`).join('');
 els=[...document.querySelectorAll('.b')];cur=-1;curBeat=-1;mark()}
function sections(){const keep=$('loop').value;$('loop').length=1;
 for(const s of D.sections){const o=document.createElement('option');o.value=s.first+'-'+s.last;
  o.textContent=`${s.letter}: bars ${s.first}-${s.last}`+(s.repeats>1?` (x${s.repeats})`:'');$('loop').append(o)}
 $('loop').append(new Option('bars...','custom'));if([...$('loop').options].some(o=>o.value===keep))$('loop').value=keep}
const barAt=n=>D.bars.find(b=>b.n===n);
function setLoop(first,last){const a=barAt(first),b=barAt(last);
 loop=a&&b&&a.start<b.end?{first,last,start:a.start+D.audio_start,end:b.end+D.audio_start}:null;
 if(loop){$('from').value=first;$('to').value=last;if(au.currentTime<loop.start||au.currentTime>=loop.end)au.currentTime=loop.start}
 mark()}
function mark(){els.forEach(e=>{const n=D.bars[+e.dataset.i].n;e.classList.toggle('loop',!!loop&&n>=loop.first&&n<=loop.last)})}
$('loop').onchange=e=>{const v=e.target.value;$('range').hidden=v!=='custom';
 if(!v){loop=null;mark()}else if(v==='custom')setLoop(+$('from').value||1,+$('to').value||Math.min(D.bars.length,8));
 else setLoop(...v.split('-').map(Number))};
$('from').onchange=$('to').onchange=()=>setLoop(+$('from').value,+$('to').value);
$('speed').onchange=e=>{au.playbackRate=e.target.value/100;store.set('speed',e.target.value)};
const sp=store.get('speed');if(sp){$('speed').value=sp;au.playbackRate=sp/100}
$('nb').onclick=$('nn').onclick=e=>{view=e.target.dataset.v;store.set('view',view);draw()};
$('grid').onclick=e=>{const el=e.target.closest('.b');if(!el||e.target.closest('input'))return;
 const b=D.bars[+el.dataset.i],beat=e.target.closest('.beat'),step=(b.end-b.start)/b.grid.length;
 au.currentTime=b.start+(beat?+beat.dataset.j*step:0)+D.audio_start;au.play()};
function tick(){const t=au.currentTime;
 // timeupdate fires only a few times a second, which would overshoot the loop end by most of a beat.
 if(loop&&!au.paused&&t>=loop.end)au.currentTime=loop.start;
 const m=t-D.audio_start;let i=-1;for(let k=0;k<D.bars.length&&D.bars[k].start<=m;k++)i=k;
 const b=D.bars[i];const j=b?Math.min(b.grid.length-1,Math.floor((m-b.start)/((b.end-b.start)/b.grid.length))):-1;
 if(i!==cur){if(els[cur])els[cur].classList.remove('now');cur=i;
  if(els[i]){els[i].classList.add('now');if($('follow').checked&&!au.paused)els[i].closest('.line').scrollIntoView({block:'center',behavior:'smooth'})}}
 const key=i+':'+j;if(key!==curBeat){document.querySelectorAll('.beat.on').forEach(x=>x.classList.remove('on'));curBeat=key;
  const box=els[i]&&els[i].querySelector(`.beat[data-j="${j}"]`);if(box)box.classList.add('on')}
 requestAnimationFrame(tick)}
if(D.edit){$('samewrap').hidden=false;$('edithelp').hidden=false;
 $('grid').ondblclick=e=>{const el=e.target.closest('.b');if(!el||el.querySelector('input'))return;
  const b=D.bars[+el.dataset.i];const inp=document.createElement('input');inp.value=b[view];
  el.lastElementChild.replaceWith(inp);inp.focus();inp.select();let done=false;
  const close=()=>{if(!done){done=true;draw()}};
  inp.onkeydown=async k=>{if(k.key==='Escape')return close();if(k.key!=='Enter')return;done=true;
   try{const r=await fetch('bar',{method:'POST',headers:{'Content-Type':'application/json','X-Omacap-Token':D.edit},
     body:JSON.stringify({n:b.n,text:inp.value,same:$('same').checked})});const j=await r.json();
    if(!r.ok){$('err').textContent='Bar '+b.n+': '+(j.error||r.status);done=false;inp.focus();return}
    $('err').textContent='';D=j;sections();draw()}catch(x){$('err').textContent='Could not save: '+x;done=false}};
  inp.onblur=()=>setTimeout(close,150)}}
sections();draw();requestAnimationFrame(tick);
</script></body></html>"""
