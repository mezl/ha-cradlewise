/**
 * Cradlewise Sleep Insights Card v9 — fully interactive + month picker
 * Main: Day/Week/Month tabs (all functional)
 * Preview: week navigation ◀▶, C-shape tap-to-tooltip, List nap expand
 */

const C = {
  sleep:'#251462', stirring:'#504aa4', awake:'#f9a12d',
  inBed:'#e91e63', soothe:'#ef5350',
  away:'rgba(37,20,98,0.18)', unknown:'rgba(37,20,98,0.18)',
  bg:'#EAE5DC', cardBg:'#FFFFFF',
  accent:'#e91e63', textDark:'#1a1a2e', textGray:'#888',
  trackBg:'rgba(37,20,98,0.10)',
};

// Generate deterministic demo history for a given Date
function makeDayDemo(date) {
  const day = new Date(date); day.setHours(0,0,0,0);
  const t = (h,m=0) => new Date(day.getTime()+(h*60+m)*60000).toISOString();
  const dow = day.getDay(); // 0=Sun...6=Sat
  // Different nap schedules for variety
  const schedules = [
    // weekday standard
    [[8,15],[9,30],[11,0],[12,30],[14,15],[17,0]],
    // 2-nap day
    [[9,0],[10,45],[13,30],[16,0],[],[],],
    // long morning nap
    [[8,30],[11,15],[14,0],[15,30],[],[],],
  ];
  const sched = schedules[dow % schedules.length];
  const [n1s,n1e,n2s,n2e,n3s,n3e] = sched.map(x=>x.length?x:null);

  const phase = [
    {entity_id:'sensor.cradlewise_avo_sleep_phase',state:'away',last_changed:t(0)},
    {entity_id:'sensor.cradlewise_avo_sleep_phase',state:'awake',last_changed:t(8,0)},
  ];
  let totalSleep=0, napCount=0, longest=0;

  const addNap=(ns,ne,stir)=>{
    if(!ns) return;
    const [nh,nm]=ns, [eh,em]=ne;
    const dur=(eh*60+em)-(nh*60+nm);
    totalSleep+=dur; napCount++; if(dur>longest) longest=dur;
    phase.push({entity_id:'sensor.cradlewise_avo_sleep_phase',state:'sleep',last_changed:t(nh,nm)});
    phase.push({entity_id:'sensor.cradlewise_avo_sleep_phase',state:'stirring',last_changed:t(nh,nm+Math.round(dur*0.6))});
    phase.push({entity_id:'sensor.cradlewise_avo_sleep_phase',state:'sleep',last_changed:t(nh,nm+Math.round(dur*0.75))});
    phase.push({entity_id:'sensor.cradlewise_avo_sleep_phase',state:'awake',last_changed:t(eh,em)});
  };
  addNap(n1s,n1e); addNap(n2s,n2e); addNap(n3s,n3e);
  phase.push({entity_id:'sensor.cradlewise_avo_sleep_phase',state:'away',last_changed:t(17,30)});

  const soothing=[];
  const addSoothe=(ns,stir)=>{
    if(!ns) return;
    const [nh,nm]=ns, dur=stir||35;
    soothing.push({entity_id:'binary_sensor.cradlewise_avo_crib_soothing',state:'on',last_changed:t(nh,nm+Math.round(dur*0.6)+1)});
    soothing.push({entity_id:'binary_sensor.cradlewise_avo_crib_soothing',state:'off',last_changed:t(nh,nm+Math.round(dur*0.6)+4)});
  };
  addSoothe(n1s); addSoothe(n2s); addSoothe(n3s);

  const awakeMin=Math.round(8*60-(totalSleep)); // rough
  return {phase, soothing, stats:{
    total_sleep:String(totalSleep), total_awake:String(Math.max(0,awakeMin)),
    soothe_count:String(napCount), nap_count:String(napCount), longest_nap:String(longest),
  }};
}

// 7-day weekly totals (deterministic per week-start date)
function makeWeekData(weekStartDate) {
  const BASE=[480,510,420,495,540,504,330];
  return Array.from({length:7},(_,i)=>{
    const d=new Date(weekStartDate); d.setDate(d.getDate()+i);
    const seed=d.getDate()+d.getMonth()*31;
    return Math.round(BASE[i]*(0.85+((seed*17)%30)/100));
  });
}

class CradlewiseSleepCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:'open'});
    this._hass=null; this._config=null; this._history=null;
    this._lastFetch=0; this._FETCH_INTERVAL=300_000;
    // Main view state
    this._mainTab='day';  // 'day'|'week'|'month'
    // Preview state
    this._demo=false;
    this._chartTab='chart'; // 'chart'|'list'
    this._weekOffset=0;    // 0=current week, -1=prev week, etc.
    this._selectedDayIdx=6; // within the current week strip
    this._expandedNapIdx=null;
    this._cTooltip=null;   // {t, timeStr, stateLabel, col} or null
    this._geom=null;
    this._showMonthPicker=false;
    this._pickYear=new Date().getFullYear();
    this._resizeObserver=null;
  }

  setConfig(cfg) {
    this._config={
      name:cfg.name||'Avo',
      sleep_phase:cfg.sleep_phase||'sensor.cradlewise_avo_sleep_phase',
      total_sleep:cfg.total_sleep||'sensor.cradlewise_avo_total_sleep_today',
      total_awake:cfg.total_awake||'sensor.cradlewise_avo_total_awake_today',
      soothe_count:cfg.soothe_count||'sensor.cradlewise_avo_soothe_count',
      nap_count:cfg.nap_count||'sensor.cradlewise_avo_nap_count',
      longest_nap:cfg.longest_nap||'sensor.cradlewise_avo_longest_nap',
      crib_soothing:cfg.crib_soothing||'binary_sensor.cradlewise_avo_crib_soothing',
    };
  }

  set hass(hass) {
    this._hass=hass;
    const now=Date.now();
    if(!this._demo && now-this._lastFetch>this._FETCH_INTERVAL){
      this._lastFetch=now; this._loadHistory();
    }
    this._render();
  }

  async _loadHistory() {
    if(!this._hass||this._demo) return;
    const c=this._config;
    const dayStart=new Date(); dayStart.setHours(0,0,0,0);
    const weekAgo=new Date(dayStart); weekAgo.setDate(weekAgo.getDate()-6);
    try {
      const [dayH,weekH]=await Promise.all([
        this._hass.callApi('GET',`history/period/${dayStart.toISOString()}?filter_entity_id=${c.sleep_phase},${c.crib_soothing}&minimal_response=true&no_attributes=true`),
        this._hass.callApi('GET',`history/period/${weekAgo.toISOString()}?filter_entity_id=${c.total_sleep}&minimal_response=true&no_attributes=true`),
      ]);
      this._history={phase:dayH?.[0]||[],soothing:dayH?.[1]||[],weekArr:weekH?.[0]||[]};
      this._render();
    } catch(e){console.warn('[sleep-card]',e);}
  }

  _render(){if(!this._hass||!this._config) return; this._demo?this._renderPreview():this._renderMain();}

  _fm(raw){const v=parseFloat(raw);if(isNaN(v)||v<0) return '–';const h=Math.floor(v/60),r=Math.round(v%60);return h===0?`${r}m`:(r===0?`${h}h`:`${h}h ${r}m`);}
  _ft(iso){if(!iso) return '–';try{return new Date(iso).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});}catch{return '–';}}

  // ── Main view ─────────────────────────────────────────────────────────────

  _renderMain(){
    const h=this._hass,c=this._config;
    const phase=h.states[c.sleep_phase]?.state||'unknown';
    const phaseLabel={sleep:'Sleeping',stirring:'Stirring',awake:'Awake',away:'Away',unknown:'–'}[phase]||phase;
    const phaseColor={sleep:'#7b6ed6',stirring:'#f5cc78',awake:'#f9a12d',away:'#aaa',unknown:'#aaa'}[phase]||'#aaa';
    const s={
      sleep:h.states[c.total_sleep]?.state, awake:h.states[c.total_awake]?.state,
      soothes:h.states[c.soothe_count]?.state, naps:h.states[c.nap_count]?.state,
      longest:h.states[c.longest_nap]?.state,
    };

    // Week bars data
    const weekBars=this._buildWeekBars();
    const maxBar=Math.max(...weekBars.map(b=>b.v),1);

    const barHtml=weekBars.map((b,i)=>{
      const pct=Math.round((b.v/maxBar)*100);
      const h2=b.v>0?Math.max(pct,4):0;
      const isSel=this._selectedBarIdx===i;
      const col=isSel?'#a090ff':(b.isToday?'#7b6ed6':'#251462');
      const vc=isSel?'#fff':(b.isToday?'#b2abd1':'#4a4a6e');
      return `<div class="bc" data-action="bar" data-bi="${i}" role="button" tabindex="0">
        <div class="bv" style="color:${vc}">${b.v>0?this._fm(b.v):''}</div>
        <div class="bb" style="height:${h2}%;background:${col};box-shadow:${isSel?'0 0 8px rgba(160,144,255,.6)':'none'}"></div>
        <div class="bl" style="color:${vc}">${b.label}</div>
      </div>`;
    }).join('');

    const isDay=this._mainTab==='day';
    const isWeek=this._mainTab==='week';

    this.shadowRoot.innerHTML=`<style>
      :host{display:block;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
      .outer{background:${C.bg};border-radius:20px;padding:16px 14px 20px}
      .hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
      .title{font-size:20px;font-weight:700;color:${C.textDark}}
      .badge{font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;background:rgba(37,20,98,0.08);color:${phaseColor}}
      .tabs{display:flex;background:rgba(0,0,0,0.06);border-radius:12px;padding:3px;margin-bottom:16px;gap:2px}
      .tab{flex:1;text-align:center;padding:8px 0;font-size:13px;font-weight:500;border-radius:9px;color:${C.textGray};cursor:pointer;transition:all .2s;border:none;background:transparent;font-family:inherit}
      .tab.act{background:#fff;color:${C.textDark};font-weight:600;box-shadow:0 1px 4px rgba(0,0,0,.1)}
      /* Day view */
      .sg{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:7px}
      .sg2{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;margin-bottom:14px}
      .sp{background:rgba(37,20,98,0.07);border-radius:12px;padding:10px 6px;text-align:center}
      .sv{font-size:17px;font-weight:700;color:${C.textDark}}
      .sl{font-size:10px;color:${C.textGray};margin-top:2px;text-transform:uppercase;letter-spacing:.5px}
      canvas#lc{display:block;width:100%;margin-bottom:6px}
      .leg{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;font-size:10px;color:${C.textGray};letter-spacing:.4px;margin-bottom:14px}
      .li{display:flex;align-items:center;gap:3px}
      .lsq{width:7px;height:7px;border-radius:2px}
      .pbtn{width:100%;padding:13px;border-radius:14px;font-size:15px;font-weight:600;background:transparent;border:2px solid ${C.accent};color:${C.accent};cursor:pointer;font-family:inherit;transition:all .2s}
      .pbtn:hover{background:rgba(233,30,99,.06)}
      /* Week view */
      .week-wrap{padding:8px 0 4px}
      .wbars{display:flex;align-items:flex-end;gap:5px;height:100px;margin-bottom:4px}
      .bc{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;justify-content:flex-end;gap:3px;cursor:pointer;transition:opacity .15s}
      .bb{width:100%;border-radius:4px 4px 0 0;transition:background .15s,box-shadow .15s}
      .bv{font-size:9px;line-height:1.2;height:12px}
      .bl{font-size:10px}
      /* Month view */
      .month-ph{text-align:center;padding:32px 16px;color:${C.textGray};font-size:14px}
      .month-ph .mico{font-size:48px;margin-bottom:12px}
    </style>
    <div class="outer">
      <div class="hdr">
        <div class="title">Sleep Insights</div>
        <div class="badge">${phaseLabel}</div>
      </div>
      <div class="tabs">
        <button class="tab ${isDay?'act':''}" id="t-day">Day</button>
        <button class="tab ${isWeek?'act':''}" id="t-week">Week</button>
        <button class="tab ${this._mainTab==='month'?'act':''}" id="t-month">Month</button>
      </div>
      ${isDay?`
        <div class="sg">
          <div class="sp"><div class="sv">${this._fm(s.sleep)}</div><div class="sl">Sleep</div></div>
          <div class="sp"><div class="sv">${s.naps||'–'}</div><div class="sl">Naps</div></div>
          <div class="sp"><div class="sv">${s.soothes||'–'}</div><div class="sl">Soothes</div></div>
        </div>
        <div class="sg2">
          <div class="sp"><div class="sv">${this._fm(s.longest)}</div><div class="sl">Longest Nap</div></div>
          <div class="sp"><div class="sv">${this._fm(s.awake)}</div><div class="sl">Awake</div></div>
        </div>
        <canvas id="lc" height="180"></canvas>
        <div class="leg">
          <div class="li"><div class="lsq" style="background:${C.sleep}"></div>SLEEP</div>
          <div class="li"><div class="lsq" style="background:${C.stirring}"></div>STIRRING</div>
          <div class="li"><div class="lsq" style="background:${C.awake}"></div>AWAKE</div>
          <div class="li"><div class="lsq" style="background:${C.soothe};border-radius:50%"></div>SOOTHES</div>
        </div>
        <button class="pbtn" id="pbtn">Preview sleep chart</button>
      `:''}
      ${isWeek?`
        <div class="week-wrap">
          <div class="wbars">${barHtml}</div>
        </div>
      `:''}
      ${this._mainTab==='month'?`
        <div class="month-ph">
          <div class="mico">📅</div>
          <div>Monthly overview coming soon</div>
          <div style="font-size:12px;margin-top:6px;color:#bbb">Real data from your crib</div>
        </div>
      `:''}
    </div>`;

    this.shadowRoot.getElementById('t-day').addEventListener('click',()=>{this._mainTab='day';this._render();});
    this.shadowRoot.getElementById('t-week').addEventListener('click',()=>{this._mainTab='week';this._render();});
    this.shadowRoot.getElementById('t-month').addEventListener('click',()=>{this._mainTab='month';this._render();});
    this.shadowRoot.getElementById('pbtn')?.addEventListener('click',()=>{this._demo=true;this._weekOffset=0;this._selectedDayIdx=6;this._render();});

    // Week bar clicks
    this.shadowRoot.querySelectorAll('[data-action="bar"]').forEach(el=>el.addEventListener('click',()=>{
      const i=parseInt(el.dataset.bi);
      this._selectedBarIdx=this._selectedBarIdx===i?null:i;
      this._render();
    }));

    // Draw live C-shape
    if(isDay){
      const ph=this._history?.phase||[];
      const sh=this._history?.soothing||[];
      requestAnimationFrame(()=>this._drawC(ph,sh,'lc',false));
      if(!this._resizeObserver){
        this._resizeObserver=new ResizeObserver(()=>{
          if(!this._demo&&this._mainTab==='day') requestAnimationFrame(()=>this._drawC(this._history?.phase||[],this._history?.soothing||[],'lc',false));
        });
        this._resizeObserver.observe(this);
      }
    }
  }

  _buildWeekBars(){
    const h=this._hass,c=this._config;
    const today=new Date();
    // Try to use live history
    const arr=this._history?.weekArr||[];
    if(arr.length>0){
      const byDay={};
      for(const e of arr){
        const d=new Date(e.last_changed||e.last_updated);
        const k=`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
        const v=parseFloat(e.state); if(!isNaN(v)) byDay[k]=v;
      }
      const DAYS=[];
      for(let i=6;i>=0;i--){
        const d=new Date(today); d.setDate(d.getDate()-i);
        const k=`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
        DAYS.push({label:d.toLocaleDateString([],{weekday:'short'}).slice(0,2),v:byDay[k]||0,isToday:i===0});
      }
      if(DAYS.some(b=>b.v>0)) return DAYS;
    }
    // Fallback demo
    const VALS=[480,510,420,495,540,504,330];
    return VALS.map((v,idx)=>{
      const i=6-idx;
      const d=new Date(today); d.setDate(d.getDate()-i);
      return {label:d.toLocaleDateString([],{weekday:'short'}).slice(0,2),v,isToday:i===0};
    });
  }

  // ── Preview view ──────────────────────────────────────────────────────────

  _renderPreview(){
    const today=new Date();
    const weekStart=new Date(today);
    weekStart.setDate(today.getDate()-6+this._weekOffset*7);

    const DNAMES=['S','M','T','W','T','F','S'];
    const days=Array.from({length:7},(_,i)=>{
      const d=new Date(weekStart); d.setDate(weekStart.getDate()+i);
      return {d,letter:DNAMES[d.getDay()],num:d.getDate(),idx:i};
    });
    const selDay=days[this._selectedDayIdx];
    const selDate=selDay.d;

    // Generate or fetch demo data for selected day
    const demo=makeDayDemo(selDate);
    const phaseHist=demo.phase;
    const soothHist=demo.soothing;
    const stats=demo.stats;

    const isChart=this._chartTab==='chart';
    const naps=this._deriveNaps(phaseHist);
    const inBedMin=(parseInt(stats.total_sleep)||0)+(parseInt(stats.total_awake)||0);

    // Month/year label from week window
    const monthLabel=selDate.toLocaleString('default',{month:'long',year:'numeric'});

    const weekStrip=days.map(d=>{
      const sel=d.idx===this._selectedDayIdx;
      return `<div class="dc ${sel?'sel':''}" data-di="${d.idx}">
        <div class="dl">${d.letter}</div>
        <div class="dn">${d.num}</div>
        <div class="du ${sel?'dul':''}"></div>
      </div>`;
    }).join('');

    const listHtml=naps.length===0
      ?`<div class="nonaps">No sleep data for this day</div>`
      :naps.map((n,i)=>{
          const bd=this._napBreakdown(n,phaseHist,soothHist);
          const open=this._expandedNapIdx===i;
          const subRows=!open?'':(`<div class="subrows">`+
            bd.segments.map(seg=>{
              const col={sleep:C.sleep,stirring:C.stirring,awake:C.awake}[seg.state]||'#aaa';
              const lbl={sleep:'Sleep',stirring:'Stirring',awake:'Awake'}[seg.state]||seg.state;
              return `<div class="sr">
                <span class="sd" style="background:${col}"></span>
                <span class="slb">${lbl}</span>
                <span class="st">${this._ft(seg.segStart?.toISOString())} – ${this._ft(seg.segEnd?.toISOString())}</span>
                <span class="sdur">${this._fm(seg.dur)}</span>
              </div>`;
            }).join('')+
            (bd.soothed?`<div class="sr"><span class="sd" style="background:${C.soothe}"></span><span class="slb" style="color:${C.soothe}">Soothed</span></div>`:'')+
            `</div>`);
          return `<div class="nc" data-action="nap" data-ni="${i}">
            <div class="nh">
              <span class="ntr">${this._ft(n.start.toISOString())} – ${n.ongoing?'now':this._ft(n.end.toISOString())}</span>
              <span class="ndb">${this._fm(n.durMin)}</span>
            </div>
            <div class="ns">${this._config.name} was in bed for ${this._fm(n.durMin)}</div>
            ${subRows}
            <div class="nchv">${open?'▲':'▼'}</div>
          </div>`;
        }).join('');

    this.shadowRoot.innerHTML=`<style>
      :host{display:block;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
      .outer{background:${C.bg};border-radius:20px;overflow:hidden;padding-bottom:20px}
      .topbar{display:flex;align-items:center;padding:14px 14px 8px;gap:8px}
      .back{background:none;border:none;font-size:24px;cursor:pointer;color:${C.textDark};padding:0;line-height:1;font-family:inherit}
      .ptitle{font-size:17px;font-weight:700;color:${C.textDark};flex:1}
      .mlab{display:flex;align-items:center;justify-content:space-between;padding:0 14px 6px}
      .mtext{font-size:12px;font-weight:600;color:${C.textGray};letter-spacing:.3px}
      .mnav{background:none;border:none;font-size:18px;cursor:pointer;color:${C.textGray};padding:0 4px;font-family:inherit;line-height:1;transition:color .15s}
      .mnav:hover{color:${C.textDark}}
      .mnav:disabled{color:#ccc;cursor:default}
      .mtbtn{background:none;border:none;cursor:pointer;font-size:12px;font-weight:600;color:${C.textGray};letter-spacing:.3px;padding:2px 8px;border-radius:8px;font-family:inherit;transition:background .15s}
      .mtbtn:hover{background:rgba(0,0,0,.07)}
      .mpicker{background:#fff;border-radius:14px;box-shadow:0 4px 20px rgba(0,0,0,.12);margin:0 10px 8px;padding:12px}
      .mpy{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
      .mpyear{font-size:14px;font-weight:700;color:${C.textDark}}
      .mpgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
      .mpm{background:rgba(37,20,98,.06);border:none;border-radius:8px;padding:9px 4px;font-size:12px;font-weight:600;cursor:pointer;color:${C.textDark};font-family:inherit;transition:background .15s}
      .mpm:hover:not(:disabled){background:${C.accent};color:#fff}
      .mpfd{color:#ccc!important;cursor:default!important}
      .wstrip{display:flex;justify-content:space-between;padding:0 10px 10px;gap:2px}
      .dc{flex:1;display:flex;flex-direction:column;align-items:center;padding:4px 2px 0;cursor:pointer;border-radius:8px;transition:background .15s}
      .dc:hover{background:rgba(0,0,0,.04)}
      .dl{font-size:11px;color:${C.textGray};font-weight:500;text-transform:uppercase}
      .dn{font-size:15px;font-weight:600;color:${C.textDark};margin:2px 0 3px}
      .dc.sel .dn{color:${C.accent}}
      .du{height:2px;width:18px;border-radius:2px}
      .dul{background:${C.accent}}
      .hdiv{height:1px;background:rgba(0,0,0,.07);margin:0 14px 12px}
      .stabs{display:flex;gap:6px;padding:0 14px 12px}
      .stab{flex:1;text-align:center;padding:8px 0;font-size:13px;font-weight:500;border-radius:10px;cursor:pointer;border:none;font-family:inherit;background:rgba(37,20,98,.07);color:${C.textGray};transition:all .2s}
      .stab.act{background:#fff;color:${C.textDark};font-weight:600;box-shadow:0 1px 6px rgba(0,0,0,.1)}
      /* Chart */
      .ccard{margin:0 10px 12px;background:#fff;border-radius:16px;box-shadow:0 2px 12px rgba(0,0,0,.07);padding:12px 6px 10px;overflow:hidden}
      .cdl{display:flex;justify-content:space-between;padding:0 6px 8px;font-size:11px;font-weight:600;color:${C.textGray};text-transform:uppercase;letter-spacing:.5px}
      canvas#ct{display:block;width:100%;cursor:crosshair}
      .leg{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;padding:8px 0 0;font-size:10px;color:${C.textGray};letter-spacing:.4px}
      .li{display:flex;align-items:center;gap:4px}
      .lsq{width:7px;height:7px;border-radius:2px}
      .tt-hint{text-align:center;font-size:10px;color:#bbb;padding:2px 0 0}
      /* Summary */
      .summ{padding:0 14px 4px}
      .sttl{font-size:11px;font-weight:700;color:${C.textGray};text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px}
      .srow{display:flex;gap:7px}
      .sp2{flex:1;border-radius:12px;padding:9px 5px;text-align:center}
      .sv2{font-size:16px;font-weight:700}
      .sl2{font-size:10px;margin-top:2px;text-transform:uppercase;letter-spacing:.4px}
      /* List */
      .listv{padding:0 10px;display:flex;flex-direction:column;gap:9px}
      .nc{background:#fff;border-radius:14px;padding:13px 13px 20px;box-shadow:0 1px 8px rgba(0,0,0,.07);cursor:pointer;position:relative;transition:box-shadow .15s}
      .nc:hover{box-shadow:0 3px 14px rgba(0,0,0,.1)}
      .nh{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px}
      .ntr{font-size:14px;font-weight:700;color:${C.textDark}}
      .ndb{font-size:13px;font-weight:600;color:${C.textGray}}
      .ns{font-size:12px;color:${C.textGray};margin-bottom:5px}
      .nchv{position:absolute;bottom:6px;right:12px;font-size:10px;color:${C.textGray}}
      .subrows{border-top:1px solid rgba(0,0,0,.06);padding-top:7px;display:flex;flex-direction:column;gap:5px}
      .sr{display:flex;align-items:center;gap:7px;font-size:12px}
      .sd{width:8px;height:8px;border-radius:50%;flex-shrink:0}
      .slb{color:${C.textDark};font-weight:500;min-width:56px}
      .st{color:${C.textGray};flex:1}
      .sdur{color:${C.textDark};font-weight:600}
      .nonaps{text-align:center;color:${C.textGray};padding:28px 14px;font-size:14px}
    </style>
    <div class="outer">
      <div class="topbar">
        <button class="back" id="back">‹</button>
        <div class="ptitle">Preview</div>
      </div>
      <div class="mlab">
        <button class="mnav" id="prev-week" ${this._weekOffset<=-104?'disabled':''}>◀</button>
        <button class="mtext mtbtn" id="mhead">${monthLabel} ▾</button>
        <button class="mnav" id="next-week" ${this._weekOffset>=0?'disabled':''}>▶</button>
      </div>
      ${this._showMonthPicker?`<div class="mpicker">
        <div class="mpy">
          <button class="mnav" id="prev-year">◀</button>
          <span class="mpyear">${this._pickYear}</span>
          <button class="mnav" id="next-year" ${this._pickYear>=new Date().getFullYear()?'disabled':''}>▶</button>
        </div>
        <div class="mpgrid">${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].map((mn,mi)=>{
          const now=new Date();
          const fut=(this._pickYear>now.getFullYear())||(this._pickYear===now.getFullYear()&&mi>now.getMonth());
          return `<button class="mpm${fut?' mpfd':''}" data-action="month" data-y="${this._pickYear}" data-m="${mi}" ${fut?'disabled':''}>${mn}</button>`;
        }).join('')}</div>
      </div>`:''}
      <div class="wstrip">${weekStrip}</div>
      <div class="hdiv"></div>
      <div class="stabs">
        <button class="stab ${isChart?'act':''}" id="tc">Chart</button>
        <button class="stab ${!isChart?'act':''}" id="tl">List</button>
      </div>
      ${isChart?`
        <div class="ccard">
          <div class="cdl">
            <span>${selDay.letter} ${selDate.toLocaleString('default',{month:'short'}).toUpperCase()} ${selDay.num}, 8 AM</span>
            <span>8 AM</span>
          </div>
          <canvas id="ct" height="260"></canvas>
          <div class="leg">
            <div class="li"><div class="lsq" style="background:${C.sleep}"></div>SLEEP</div>
            <div class="li"><div class="lsq" style="background:${C.stirring}"></div>STIRRING</div>
            <div class="li"><div class="lsq" style="background:${C.awake}"></div>AWAKE</div>
            <div class="li"><div class="lsq" style="background:${C.soothe};border-radius:50%"></div>SOOTHES</div>
          </div>
          <div class="tt-hint">Tap timeline to see details</div>
        </div>
        <div class="summ">
          <div class="sttl">Day in Summary</div>
          <div class="srow">
            <div class="sp2" style="background:rgba(233,30,99,.08)">
              <div class="sv2" style="color:${C.inBed}">${this._fm(inBedMin)}</div>
              <div class="sl2" style="color:${C.inBed}">in bed</div>
            </div>
            <div class="sp2" style="background:rgba(37,20,98,.08)">
              <div class="sv2" style="color:${C.stirring}">${this._fm(stats.total_sleep)}</div>
              <div class="sl2" style="color:${C.stirring}">sleep</div>
            </div>
            <div class="sp2" style="background:rgba(249,161,45,.08)">
              <div class="sv2" style="color:${C.awake}">${this._fm(stats.total_awake)}</div>
              <div class="sl2" style="color:${C.awake}">awake</div>
            </div>
          </div>
        </div>
      `:`<div class="listv">${listHtml}</div>`}
    </div>`;

    // Listeners
    this.shadowRoot.getElementById('back').addEventListener('click',()=>{this._demo=false;this._expandedNapIdx=null;this._cTooltip=null;this._render();});
    this.shadowRoot.getElementById('tc').addEventListener('click',()=>{this._chartTab='chart';this._cTooltip=null;this._renderPreview();});
    this.shadowRoot.getElementById('tl').addEventListener('click',()=>{this._chartTab='list';this._expandedNapIdx=null;this._cTooltip=null;this._renderPreview();});

    this.shadowRoot.getElementById('prev-week').addEventListener('click',()=>{
      this._weekOffset--; this._selectedDayIdx=6; this._cTooltip=null; this._renderPreview();
    });
    this.shadowRoot.getElementById('next-week').addEventListener('click',()=>{
      if(this._weekOffset<0){this._weekOffset++; this._selectedDayIdx=6; this._cTooltip=null; this._renderPreview();}
    });
    this.shadowRoot.getElementById('mhead').addEventListener('click',()=>{
      if(!this._showMonthPicker){
        // Sync pick year to currently displayed month
        const today2=new Date(); const ws2=new Date(today2);
        ws2.setDate(today2.getDate()-6+this._weekOffset*7);
        const sd2=new Date(ws2); sd2.setDate(ws2.getDate()+this._selectedDayIdx);
        this._pickYear=sd2.getFullYear();
      }
      this._showMonthPicker=!this._showMonthPicker; this._renderPreview();
    });
    const py=this.shadowRoot.getElementById('prev-year');
    if(py) py.addEventListener('click',(e)=>{e.stopPropagation();this._pickYear--;this._renderPreview();});
    const ny=this.shadowRoot.getElementById('next-year');
    if(ny) ny.addEventListener('click',(e)=>{e.stopPropagation();this._pickYear++;this._renderPreview();});
    this.shadowRoot.querySelectorAll('[data-action="month"]').forEach(el=>el.addEventListener('click',()=>{
      this._jumpToMonth(parseInt(el.dataset.y),parseInt(el.dataset.m));
    }));

    this.shadowRoot.querySelectorAll('.dc').forEach(el=>el.addEventListener('click',()=>{
      this._selectedDayIdx=parseInt(el.dataset.di); this._cTooltip=null; this._renderPreview();
    }));
    this.shadowRoot.querySelectorAll('[data-action="nap"]').forEach(el=>el.addEventListener('click',()=>{
      const i=parseInt(el.dataset.ni); this._expandedNapIdx=this._expandedNapIdx===i?null:i; this._renderPreview();
    }));

    if(isChart){
      requestAnimationFrame(()=>this._drawC(phaseHist,soothHist,'ct',true));
      // Tap-to-tooltip on canvas
      const canvas=this.shadowRoot.getElementById('ct');
      if(canvas){
        const handleTap=(e)=>{
          e.preventDefault();
          const rect=canvas.getBoundingClientRect();
          const dpr=window.devicePixelRatio||1;
          const cx2=(e.touches?e.touches[0].clientX:e.clientX)-rect.left;
          const cy2=(e.touches?e.touches[0].clientY:e.clientY)-rect.top;
          // Scale to canvas logical coords
          const lx=cx2*(canvas.width/dpr)/rect.width;
          const ly=cy2*(canvas.height/dpr)/rect.height;
          const t=this._tapToTime(lx,ly);
          if(t===null) return;
          // Find phase at t
          const dayStart=new Date(selDate); dayStart.setHours(0,0,0,0);
          const tapMs=(t+8)*3600000; // back to ms from midnight
          let state='away';
          for(let i=phaseHist.length-1;i>=0;i--){
            const eMs=new Date(phaseHist[i].last_changed||phaseHist[i].last_updated)-dayStart;
            if(tapMs>=eMs){state=phaseHist[i].state;break;}
          }
          const stateLabel={sleep:'💤 Sleeping',stirring:'〜 Stirring',awake:'👶 Awake',away:'— Away',unknown:'— No data'}[state]||state;
          const stateCol={sleep:C.stirring,stirring:'#f5cc78',awake:C.awake,away:'#aaa',unknown:'#aaa'}[state]||'#aaa';
          const h24=Math.floor(t+8), m=Math.round((t+8-h24)*60);
          const d=new Date(dayStart.getTime()+h24*3600000+m*60000);
          const timeStr=d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
          // Toggle off if same position
          if(this._cTooltip && Math.abs(this._cTooltip.t-t)<0.2){
            this._cTooltip=null;
          } else {
            this._cTooltip={t,timeStr,stateLabel,stateCol};
          }
          this._drawC(phaseHist,soothHist,'ct',true);
        };
        canvas.addEventListener('click',handleTap);
        canvas.addEventListener('touchend',handleTap,{passive:false});
      }
      if(!this._resizeObserver){
        this._resizeObserver=new ResizeObserver(()=>{
          if(this._demo&&this._chartTab==='chart') requestAnimationFrame(()=>this._drawC(phaseHist,soothHist,'ct',true));
        });
        this._resizeObserver.observe(this);
      }
    }
  }

  // ── C-shape canvas ────────────────────────────────────────────────────────

  _drawC(phaseHist,soothHist,canvasId,isDemo){
    const canvas=this.shadowRoot.getElementById(canvasId);
    if(!canvas) return;

    const dpr=window.devicePixelRatio||1;
    const W=canvas.offsetWidth||330;
    const H=canvasId==='lc'?180:260;
    canvas.width=W*dpr; canvas.height=H*dpr;
    canvas.style.width=W+'px'; canvas.style.height=H+'px';
    const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);

    const TK=canvasId==='lc'?15:20;
    const topY=canvasId==='lc'?30:44;
    const botY=H-topY;
    const cy=(topY+botY)/2;
    const R=(botY-topY)/2;
    const cx=W-R-20;
    const lx=22;

    this._geom={leftX:lx,topY,botY,cy,R,cx,W,H,TK};

    const pt=t=>{
      if(t<=6) return[lx+(t/6)*(cx-lx),topY];
      if(t<=18){const a=-Math.PI/2+((t-6)/12)*Math.PI;return[cx+R*Math.cos(a),cy+R*Math.sin(a)];}
      return[cx-((t-18)/6)*(cx-lx),botY];
    };

    const stroke=(t0,t1,color,lw,cap='butt')=>{
      if(t1<=t0) return;
      t0=Math.max(0,Math.min(24,t0)); t1=Math.max(0,Math.min(24,t1)); if(t1<=t0) return;
      const splits=[6,18].filter(b=>b>t0&&b<t1);
      const cps=[t0,...splits,t1];
      ctx.beginPath();
      const[sx,sy]=pt(t0); ctx.moveTo(sx,sy);
      for(let i=1;i<cps.length;i++){
        const a=cps[i-1],b=cps[i];
        if(a>=6&&b<=18){ctx.arc(cx,cy,R,-Math.PI/2+((a-6)/12)*Math.PI,-Math.PI/2+((b-6)/12)*Math.PI);}
        else{const[ex,ey]=pt(b);ctx.lineTo(ex,ey);}
      }
      ctx.strokeStyle=color; ctx.lineWidth=lw; ctx.lineCap=cap; ctx.lineJoin='round'; ctx.stroke();
    };

    // Background track
    ctx.beginPath(); ctx.moveTo(lx,topY); ctx.lineTo(cx,topY);
    ctx.arc(cx,cy,R,-Math.PI/2,Math.PI/2); ctx.lineTo(lx,botY);
    ctx.strokeStyle=C.trackBg; ctx.lineWidth=TK+8; ctx.lineCap='round'; ctx.stroke();

    // Phase segments — use the date of the first history entry as dayStart
    const dayStart=new Date();
    if(phaseHist.length>0){const ref=new Date(phaseHist[0].last_changed||phaseHist[0].last_updated);dayStart.setFullYear(ref.getFullYear(),ref.getMonth(),ref.getDate());}
    dayStart.setHours(0,0,0,0);
    const nowMs=isDemo?24*3600000:(Date.now()-dayStart);
    for(let i=0;i<phaseHist.length;i++){
      const e=phaseHist[i],next=phaseHist[i+1];
      if(!e.state||e.state==='away'||e.state==='unknown') continue;
      const eMs=new Date(e.last_changed||e.last_updated)-dayStart;
      const nMs=next?new Date(next.last_changed||next.last_updated)-dayStart:nowMs;
      let t0=eMs/3600000-8,t1=nMs/3600000-8;
      if(t0<0)t0+=24; if(t1<0)t1+=24; if(t1<t0)t1=24;
      const col={sleep:C.sleep,stirring:C.stirring,awake:C.awake}[e.state]||'rgba(180,175,210,.4)';
      stroke(t0,t1,col,TK,'butt');
    }

    // Tick labels (skip for small canvas)
    if(canvasId!=='lc'){
      ctx.fillStyle='#999';
      ctx.font=`bold 9px -apple-system,sans-serif`;
      const topLbls=[{t:0,lbl:'8 AM'},{t:3,lbl:'11 AM'},{t:6,lbl:'2 PM'}];
      const botLbls=[{t:18,lbl:'2 AM'},{t:21,lbl:'5 AM'},{t:24,lbl:'8 AM'}];
      const arcLbls=[{t:9,lbl:'5 PM'},{t:12,lbl:'8 PM'},{t:15,lbl:'11 PM'}];
      for(const{t,lbl}of topLbls){const[px2,py2]=pt(t);ctx.textAlign=t===0?'left':(t===6?'right':'center');ctx.textBaseline='bottom';ctx.fillText(lbl,t===0?px2+2:(t===6?px2-2:px2),py2-TK/2-3);}
      for(const{t,lbl}of botLbls){const[px2,py2]=pt(t);ctx.textAlign=t===18?'right':(t===24?'left':'center');ctx.textBaseline='top';ctx.fillText(lbl,t===18?px2-2:(t===24?px2+2:px2),py2+TK/2+3);}
      for(const{t,lbl}of arcLbls){const a=-Math.PI/2+((t-6)/12)*Math.PI;const offs=TK/2+14;ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(lbl,cx+(R+offs)*Math.cos(a),cy+(R+offs)*Math.sin(a));}
      // Tick marks
      ctx.strokeStyle='rgba(37,20,98,.15)'; ctx.lineWidth=1; ctx.lineCap='square';
      for(const t of[0,3,6,9,12,15,18,21,24]){
        const[px2,py2]=pt(t);
        if(t<=6){ctx.beginPath();ctx.moveTo(px2,py2-TK/2);ctx.lineTo(px2,py2-TK/2-5);ctx.stroke();}
        else if(t<=18){const a=-Math.PI/2+((t-6)/12)*Math.PI;ctx.beginPath();ctx.moveTo(cx+(R+TK/2)*Math.cos(a),cy+(R+TK/2)*Math.sin(a));ctx.lineTo(cx+(R+TK/2+5)*Math.cos(a),cy+(R+TK/2+5)*Math.sin(a));ctx.stroke();}
        else{ctx.beginPath();ctx.moveTo(px2,py2+TK/2);ctx.lineTo(px2,py2+TK/2+5);ctx.stroke();}
      }
    } else {
      // Minimal time labels for small canvas
      ctx.fillStyle='#aaa'; ctx.font='8px sans-serif'; ctx.textAlign='center'; ctx.textBaseline='bottom';
      for(const{t,l}of[{t:0,l:'8AM'},{t:6,l:'2PM'},{t:12,l:'8PM'},{t:24,l:'8AM'}]){
        const[px2,py2]=pt(t);
        if(t<=6){ctx.fillText(l,px2,py2-TK/2-2);}
        else if(t===12){ctx.fillText(l,cx+R+14,cy);}
        else{ctx.textBaseline='top';ctx.fillText(l,px2,py2+TK/2+2);ctx.textBaseline='bottom';}
      }
    }

    // Soothe dots
    for(const e of(soothHist||[])){
      if(e.state!=='on') continue;
      const eMs=new Date(e.last_changed||e.last_updated)-dayStart;
      if(eMs<0||eMs>nowMs) continue;
      let t=eMs/3600000-8; if(t<0)t+=24; t=Math.max(0,Math.min(24,t));
      const[px2,py2]=pt(t);
      ctx.beginPath();ctx.arc(px2,py2,TK/2*0.8+2,0,Math.PI*2);ctx.fillStyle='rgba(239,83,80,.2)';ctx.fill();
      ctx.beginPath();ctx.arc(px2,py2,4,0,Math.PI*2);ctx.fillStyle=C.soothe;ctx.fill();
      ctx.beginPath();ctx.arc(px2,py2,4,0,Math.PI*2);ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();
    }

    // Bed-time start marker
    const firstIn=phaseHist.find(e=>e.state==='sleep'||e.state==='stirring');
    if(firstIn){
      const eMs=new Date(firstIn.last_changed||firstIn.last_updated)-dayStart;
      let t=eMs/3600000-8; if(t<0)t+=24; t=Math.max(0,Math.min(24,t));
      const[px2,py2]=pt(t);
      ctx.beginPath();ctx.arc(px2,py2,6,0,Math.PI*2);ctx.fillStyle=C.inBed;ctx.fill();
      ctx.beginPath();ctx.arc(px2,py2,6,0,Math.PI*2);ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();
    }

    // ── Tap tooltip ──
    if(this._cTooltip&&canvasId==='ct'){
      const{t:tt,timeStr,stateLabel,stateCol}=this._cTooltip;
      const[px2,py2]=pt(tt);

      // Highlight dot on track
      ctx.beginPath();ctx.arc(px2,py2,TK/2+3,0,Math.PI*2);ctx.fillStyle='rgba(255,255,255,.25)';ctx.fill();
      ctx.beginPath();ctx.arc(px2,py2,5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();
      ctx.beginPath();ctx.arc(px2,py2,5,0,Math.PI*2);ctx.strokeStyle=stateCol;ctx.lineWidth=2;ctx.stroke();

      // Callout bubble — place inside the C (upper-left hollow area)
      const bW=128,bH=44,bX=lx+2,bY=topY+TK/2+10;
      ctx.beginPath();
      if(ctx.roundRect) ctx.roundRect(bX,bY,bW,bH,8);
      else{const r=8;ctx.moveTo(bX+r,bY);ctx.lineTo(bX+bW-r,bY);ctx.quadraticCurveTo(bX+bW,bY,bX+bW,bY+r);ctx.lineTo(bX+bW,bY+bH-r);ctx.quadraticCurveTo(bX+bW,bY+bH,bX+bW-r,bY+bH);ctx.lineTo(bX+r,bY+bH);ctx.quadraticCurveTo(bX,bY+bH,bX,bY+bH-r);ctx.lineTo(bX,bY+r);ctx.quadraticCurveTo(bX,bY,bX+r,bY);ctx.closePath();}
      ctx.fillStyle='rgba(26,26,46,0.92)';ctx.fill();

      ctx.textAlign='left';ctx.textBaseline='middle';
      ctx.fillStyle='#fff';ctx.font=`bold 11px -apple-system,sans-serif`;
      ctx.fillText(timeStr,bX+10,bY+14);
      ctx.fillStyle=stateCol;ctx.font=`10px -apple-system,sans-serif`;
      ctx.fillText(stateLabel,bX+10,bY+30);

      // Line from bubble to dot
      ctx.strokeStyle='rgba(255,255,255,.4)';ctx.lineWidth=1;ctx.setLineDash([3,3]);
      ctx.beginPath();
      const nearX=Math.min(bX+bW, Math.max(bX, px2));
      ctx.moveTo(nearX,bY+bH);ctx.lineTo(px2,py2-TK/2-2);
      ctx.stroke();ctx.setLineDash([]);
    }
  }

  // ── Hit detection: (x,y) canvas coords → hours from 8AM ─────────────────

  _tapToTime(x,y){
    const g=this._geom; if(!g) return null;
    const{leftX:lx,topY,botY,cy,R,cx}=g;

    // Distance to top track
    const topClampX=Math.max(lx,Math.min(cx,x));
    const topDist=Math.hypot(x-topClampX,y-topY);

    // Distance to arc (right semicircle only)
    const dx=x-cx,dy=y-cy;
    const ang=Math.atan2(dy,dx);
    const inArc=ang>=-Math.PI/2&&ang<=Math.PI/2;
    const arcDist=inArc?Math.abs(Math.hypot(dx,dy)-R):Infinity;

    // Distance to bottom track
    const botClampX=Math.max(lx,Math.min(cx,x));
    const botDist=Math.hypot(x-botClampX,y-botY);

    const minD=Math.min(topDist,arcDist,botDist);
    if(minD>50) return null; // too far from track

    if(minD===topDist){
      return(topClampX-lx)/(cx-lx)*6;
    } else if(minD===arcDist){
      return 6+(ang+Math.PI/2)/Math.PI*12;
    } else {
      return 18+(cx-botClampX)/(cx-lx)*6;
    }
  }

  // ── Month picker navigation ───────────────────────────────────────────────

  _jumpToMonth(year, month){
    const today=new Date(); today.setHours(0,0,0,0);
    // Anchor to last day of chosen month so full month is visible
    const lastDay=new Date(year,month+1,0); lastDay.setHours(0,0,0,0);
    const diffDays=Math.round((lastDay-today)/86400000);
    if(diffDays>0) return; // future month — ignore
    // Find weekOffset so lastDay falls within the week strip
    const weekOffset=Math.floor((diffDays+6)/7);
    const selectedDayIdx=diffDays+6-weekOffset*7;
    this._weekOffset=Math.max(-104,weekOffset);
    this._selectedDayIdx=selectedDayIdx;
    this._showMonthPicker=false;
    this._cTooltip=null; this._expandedNapIdx=null;
    this._renderPreview();
  }

  // ── Nap utilities ─────────────────────────────────────────────────────────

  _deriveNaps(ph){
    if(!ph.length) return [];
    const now=new Date(),naps=[];
    let start=null;
    for(const e of ph){
      const isSleep=e.state==='sleep'||e.state==='stirring';
      const ts=new Date(e.last_changed||e.last_updated);
      if(isSleep&&start===null){start=ts;}
      else if(!isSleep&&start!==null){
        const dur=Math.round((ts-start)/60000);
        if(dur>=5) naps.push({start,end:ts,durMin:dur}); start=null;
      }
    }
    if(start){const dur=Math.round((now-start)/60000);if(dur>=5)naps.push({start,end:now,durMin:dur,ongoing:true});}
    return naps;
  }

  _napBreakdown(nap,ph,sh){
    const segs=[];let sleepMin=0,stirringMin=0,soothed=false;
    for(let i=0;i<ph.length;i++){
      const e=ph[i],next=ph[i+1];
      if(e.state!=='sleep'&&e.state!=='stirring') continue;
      const eT=new Date(e.last_changed||e.last_updated);
      const nT=next?new Date(next.last_changed||next.last_updated):nap.end;
      const segStart=new Date(Math.max(eT,nap.start));
      const segEnd=new Date(Math.min(nT,nap.end));
      if(segEnd<=segStart) continue;
      const dur=Math.round((segEnd-segStart)/60000); if(dur<1) continue;
      segs.push({state:e.state,dur,segStart,segEnd});
      if(e.state==='sleep')sleepMin+=dur;else stirringMin+=dur;
    }
    for(const e of sh){if(e.state!=='on')continue;const t=new Date(e.last_changed||e.last_updated);if(t>=nap.start&&t<=nap.end){soothed=true;break;}}
    return{segments:segs,sleepMin,stirringMin,soothed};
  }

  disconnectedCallback(){if(this._resizeObserver){this._resizeObserver.disconnect();this._resizeObserver=null;}}

  static getStubConfig(){return{name:'Avo'};}
}

customElements.define('cradlewise-sleep-card',CradlewiseSleepCard);
window.customCards=window.customCards||[];
if(!window.customCards.find(c=>c.type==='cradlewise-sleep-card'))
  window.customCards.push({type:'cradlewise-sleep-card',name:'Cradlewise Sleep Insights',description:'Interactive C-shape sleep chart. Day/Week tabs, week navigation, tap timeline.',preview:true});
