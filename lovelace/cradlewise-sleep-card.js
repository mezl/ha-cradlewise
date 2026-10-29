/**
 * Cradlewise Sleep Insights Card v21 — perf: render only when the card's own entities change
 * (v20: cached day-totals, lazy 62-day month fetch)
 * Main: Day/Week/Month tabs (all functional)
 * Preview: week navigation ◀▶, C-shape tap-to-tooltip, List nap expand
 *
 * v20: week/month lag fix. v19 re-fetched 62 days (~15k rows) every 5 min and
 * re-parsed ALL rows on EVERY render (one per HA state update) to derive daily
 * totals. Now: the recurring fetch is 8 days; the per-day map is computed once
 * per fetch and cached; the 62-day payload loads only when the Month tab is
 * opened (or month data goes stale), throttled to 30 min.
 *
 * v21: the actual lag. HA calls `set hass` for EVERY state change in the house
 * (measured 13-18/s on this install) and each call rebuilt the whole shadow DOM,
 * so a tap landing between press and release hit a destroyed element — only
 * 1 of 20 slow taps registered. Now `set hass` re-renders only when one of the
 * card's own entities changed, renders are coalesced to one per frame, and a
 * render waits while a pointer is down.
 *
 * v22: Week tab gets ◀ ▶ week navigation; tapping a week bar or a month-calendar
 * day opens that day's detail chart. Past weeks/months load per calendar month
 * (cached), so navigation is no longer limited to the last 8/62 days.
 */

const CARD_VERSION='v23 (r33)';

const C = {
  sleep:'#251462', stirring:'#504aa4', awake:'#f9a12d',
  inBed:'#e91e63', soothe:'#ef5350',
  away:'rgba(37,20,98,0.18)', unknown:'rgba(37,20,98,0.18)',
  bg:'#EAE5DC', cardBg:'#FFFFFF',
  accent:'#e91e63', textDark:'#1a1a2e', textGray:'#888',
  trackBg:'rgba(37,20,98,0.10)',
};

class CradlewiseSleepCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:'open'});
    this._hass=null; this._config=null; this._history=null; this._historyErr=null;
    this._lastFetch=0; this._FETCH_INTERVAL=300_000;
    // Month-tab history is a separate, much bigger (62-day) fetch — loaded lazily
    // and throttled, so opening the card / Day tab never pulls it.
    this._monthByDay=null; this._monthFetch=0; this._MONTH_FETCH_INTERVAL=1_800_000;
    // Main view state
    this._mainTab='day';  // 'day'|'week'|'month'
    // Preview state
    this._demo=false;
    this._chartTab='chart'; // 'chart'|'list'
    this._weekOffset=0;    // 0=current week, -1=prev week, etc.
    this._monthOffset=0;   // 0=current month, -1=prev month (month tab)
    this._mainWeekOffset=0; // 0=week ending today, -1=the 7 days before (week tab)
    this._monthCache={};   // 'y-m' -> {loading, at, byDay} sleep-day totals per calendar month
    this._selectedDayIdx=6; // within the current week strip
    this._expandedNapIdx=null;
    this._cTooltip=null;   // {t, timeStr, stateLabel, col} or null
    this._geom=null;
    this._showMonthPicker=false;
    this._pickYear=new Date().getFullYear();
    this._resizeObserver=null;
    // Render scheduling (see v21 note at top).
    this._watchStates=null;   // last-rendered state objects of the card's entities
    this._renderPending=false;
    this._ptrDown=0;          // timestamp of an unreleased pointerdown, or 0
    this.shadowRoot.addEventListener('pointerdown',()=>{this._ptrDown=Date.now();});
    const release=()=>{this._ptrDown=0; if(this._renderPending){this._renderPending=false;this._scheduleRender();}};
    this.shadowRoot.addEventListener('pointerup',release);
    this.shadowRoot.addEventListener('pointercancel',release);
    this.shadowRoot.addEventListener('pointerleave',release);
  }

  setConfig(cfg) {
    this._config={
      name:cfg.name||'Avo',
      // sensor.cradlewise_avo_sleep_phase reads the babySleepPhase shadow key,
      // which this crib pins at 0 ("away") forever. babySleepState — exposed as
      // the sleep_state sensor — is the one that actually moves, so prefer it and
      // keep sleep_phase as a fallback for builds that only ship that.
      sleep_phase:cfg.sleep_phase||['sensor.cradlewise_avo_sleep_state','sensor.cradlewise_avo_sleep_phase'],
      total_sleep:cfg.total_sleep||['sensor.cradlewise_avo_total_sleep_today'],
      total_awake:cfg.total_awake||['sensor.cradlewise_avo_total_awake_today'],
      soothe_count:cfg.soothe_count||['sensor.cradlewise_avo_soothing_count_today','sensor.cradlewise_avo_soothe_count'],
      nap_count:cfg.nap_count||['sensor.cradlewise_avo_nap_count_today','sensor.cradlewise_avo_nap_count'],
      longest_nap:cfg.longest_nap||['sensor.cradlewise_avo_longest_nap_today','sensor.cradlewise_avo_longest_nap'],
      crib_soothing:cfg.crib_soothing||['binary_sensor.cradlewise_avo_crib_soothing'],
    };
    // Every entity id the card can read (all candidates, so a fallback that
    // comes back to life still triggers a render).
    const {name,...ents}=this._config;
    this._watchIds=[...new Set(Object.values(ents).flat())];
    this._watchStates=null;
  }

  // True when any watched entity's state object changed since the last check.
  // HA replaces a state object whenever its state or attributes change, so a
  // reference compare is exact and costs ~10 lookups.
  _statesChanged(hass){
    const cur=(this._watchIds||[]).map(id=>hass.states[id]);
    const prev=this._watchStates;
    this._watchStates=cur;
    return !prev||cur.length!==prev.length||cur.some((st,i)=>st!==prev[i]);
  }

  // One render per animation frame at most; held while a pointer is down so a
  // press is never split across a DOM rebuild (held max 1.5 s, in case the
  // release happened outside the card).
  _scheduleRender(){
    if(this._renderPending) return;
    this._renderPending=true;
    requestAnimationFrame(()=>{
      if(this._ptrDown&&Date.now()-this._ptrDown<1500){
        setTimeout(()=>{this._renderPending=false;this._scheduleRender();},100);
        return;
      }
      this._renderPending=false;
      this._render();
    });
  }

  // Entity ids may be a single id (from user config) or a list of candidates.
  // Older integration builds name these sensors without the "_today" suffix,
  // and a renamed sensor is left behind in the registry as a permanently
  // unavailable entity — so pick the first candidate that actually has a
  // usable state, not merely the first that exists.
  _pick(cand){
    const list=Array.isArray(cand)?cand:[cand];
    for(const id of list){ if(this._live(id)) return id; }
    return list[0];
  }
  _live(id){
    const e=this._hass?.states[id];
    return !!e && e.state!=='unavailable' && e.state!=='unknown';
  }

  // ── Per-day history ───────────────────────────────────────────────────────

  _dayKey(d){return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;}

  // Fetch one calendar day of phase/soothe history. Results are cached per day;
  // today's entry is short-lived so it keeps up with the live crib.
  _fetchDay(date){
    const key=this._dayKey(date);
    this._dayCache=this._dayCache||{};
    const hit=this._dayCache[key];
    const isToday=this._dayKey(new Date())===key;
    if(hit&&(hit.loading||!isToday||Date.now()-hit.at<this._FETCH_INTERVAL)) return hit;

    const entry=this._dayCache[key]={loading:true,at:Date.now(),phase:[],soothing:[]};
    const dayStart=new Date(date); dayStart.setHours(0,0,0,0);
    const dayEnd=new Date(dayStart); dayEnd.setDate(dayEnd.getDate()+1);
    const c=this._config;
    // end_time is NOT optional: the history API defaults to start+1day, so a
    // multi-day range silently returns only its first day.
    // The daily-total sensors reset at midnight and only climb, so their peak
    // within a day IS that day's total — and it is what the tiles showed at the
    // time. Far better than re-deriving totals from sleep_state, which only
    // covers whatever part of the day the recorder happened to be up for.
    const totalIds=[c.total_sleep,c.total_awake,c.nap_count,c.longest_nap,c.soothe_count]
      .map(x=>this._pick(x));
    const period=(ids)=>`history/period/${dayStart.toISOString()}`
      +`?filter_entity_id=${ids}`
      +`&end_time=${encodeURIComponent(dayEnd.toISOString())}`
      +`&minimal_response=true&no_attributes=true`;
    const url=period(`${this._pick(c.sleep_phase)},${this._pick(c.crib_soothing)}`);
    Promise.all([
      this._hass.callApi('GET',url),
      isToday?Promise.resolve(null):this._hass.callApi('GET',period(totalIds.join(','))),
    ]).then(([res,totalsRes])=>{
      const ph=res?.[0]||[],so=res?.[1]||[];
      entry.phase=ph.filter(e=>e.state&&e.state!=='unavailable');
      entry.soothing=so;
      if(totalsRes) entry.totals=this._peakTotals(totalsRes,totalIds);
      entry.loading=false; entry.at=Date.now();
      if(isToday) this._backfillToday(entry,dayStart);
      this._scheduleRender();
    }).catch(e=>{
      entry.loading=false; entry.error=e; entry.at=Date.now();
      if(isToday){const fb=this._napTimeline(); entry.phase=fb.phase; entry.soothing=fb.soothing;}
      this._scheduleRender();
    });
    return entry;
  }

  // Recorder only covers today from whenever it started (a restart, or an
  // outage that ended mid-day). The integration's own tracker covers the whole
  // day but has no stirring sub-phases — so splice: tracker before recorder's
  // first real row, recorder after. Without this the two views of "today"
  // disagree, one showing naps the other has never heard of.
  _backfillToday(entry,dayStart){
    const fb=this._napTimeline();
    if(!fb.phase.length) return;
    const ms=e=>new Date(e.last_changed||e.last_updated).getTime();
    const dayStartMs=dayStart.getTime();
    // The first row of a history response is the state carried in at the window
    // edge, stamped at dayStart — not evidence that anything was recorded.
    const firstReal=entry.phase.find(e=>ms(e)>dayStartMs);
    const coverMs=firstReal?ms(firstReal):Infinity;
    const pre=fb.phase.filter(e=>ms(e)<coverMs);
    if(!pre.length) return;
    entry.phase=[...pre,...entry.phase.filter(e=>ms(e)>=coverMs)];
    entry.soothing=[...fb.soothing.filter(e=>ms(e)<coverMs),...(entry.soothing||[]).filter(e=>ms(e)>=coverMs)];
  }

  // Highest value each daily-total sensor reached on that day.
  _peakTotals(res,ids){
    const peak=id=>{
      const arr=(res||[]).find(a=>a[0]?.entity_id===id);
      if(!arr) return null;
      let m=null;
      for(const e of arr){
        const v=parseFloat(e.state);
        if(!isNaN(v)&&(m===null||v>m)) m=v;
      }
      return m;
    };
    const [sleep,awake,naps,longest,soothes]=ids.map(peak);
    if(sleep===null&&naps===null) return null;
    return {
      total_sleep:String(sleep??0),
      total_awake:String(awake??0),
      nap_count:String(naps??0),
      longest_nap:String(longest??0),
      soothe_count:String(soothes??0),
    };
  }

  // Today's timeline synthesised from the integration's tracker attributes.
  // Sleep and awake are both intervals the tracker actually observed; anything
  // outside them stays 'away', because a gap between naps may well have been
  // time out of the crib rather than awake in it.
  _napTimeline(){
    const naps=this._attr(this._config.nap_count,'naps');
    const awake=this._attr(this._config.nap_count,'awake_periods');
    const soothes=this._attr(this._config.nap_count,'soothing_events');
    if(!Array.isArray(naps)) return {phase:[],soothing:[]};
    const dayStart=new Date(); dayStart.setHours(0,0,0,0);
    const phase=[{state:'away',last_changed:dayStart.toISOString()}];
    const add=(list,state)=>{
      for(const iv of (Array.isArray(list)?list:[])){
        if(!iv||!iv.start) continue;
        phase.push({state,last_changed:iv.start});
        // An interval with no end is still running — leave it open so the
        // chart carries it to "now" instead of closing it early.
        if(iv.end) phase.push({state:'away',last_changed:iv.end});
      }
    };
    add(naps,'sleep');
    add(awake,'awake');
    phase.sort((a,b)=>new Date(a.last_changed)-new Date(b.last_changed));
    return {phase,soothing:(Array.isArray(soothes)?soothes:[]).map(ts=>({state:'on',last_changed:ts}))};
  }

  // Sleep/awake/soothe totals for one day, measured off the timeline itself so
  // a past day reports that day's numbers rather than today's live sensors.
  _isToday(d){return this._dayKey(d)===this._dayKey(new Date());}

  // Stats for a day. For today the integration's own sensors ARE the answer —
  // the same ones the tiles show — so read them rather than recomputing from
  // the timeline and inevitably landing on a slightly different number.
  // Only a past day, which no live sensor describes, is measured off history.
  _dayStats(phase,sooth,date){
    if(this._isToday(date)){
      const c=this._config;
      const sleep=this._st(c.total_sleep), awake=this._st(c.total_awake);
      const naps=this._st(c.nap_count), longest=this._st(c.longest_nap);
      const soothes=this._st(c.soothe_count);
      if(sleep!=null||naps!=null){
        return {
          total_sleep:sleep??'0',
          total_awake:awake??'0',
          nap_count:naps??'0',
          longest_nap:longest??'0',
          soothe_count:soothes??'0',
        };
      }
    }
    // A past day: prefer what the daily-total sensors actually recorded.
    const d=this._dayCache?.[this._dayKey(date)];
    if(d&&d.totals) return d.totals;
    return this._statsFromTimeline(phase,sooth,date);
  }

  // Nap list for a day. Today's comes straight from the tracker's own sessions
  // so the list, the NAPS tile and the summary all describe the same naps.
  _dayNaps(phase,date){
    if(this._isToday(date)){
      const sessions=this._attr(this._config.nap_count,'naps');
      if(Array.isArray(sessions)&&sessions.length){
        const now=new Date();
        return sessions.filter(n=>n&&n.start).map(n=>({
          start:new Date(n.start),
          end:n.end?new Date(n.end):now,
          durMin:Math.round(n.minutes??0),
          ongoing:!n.end,
        }));
      }
    }
    return this._deriveNaps(phase);
  }

  _statsFromTimeline(phase,sooth,date){
    const dayStart=new Date(date); dayStart.setHours(0,0,0,0);
    const dayEnd=new Date(dayStart); dayEnd.setDate(dayEnd.getDate()+1);
    const cutoff=Math.min(dayEnd.getTime(),Date.now());
    let sleep=0,awake=0;
    for(let i=0;i<phase.length;i++){
      const st=phase[i].state;
      const from=new Date(phase[i].last_changed||phase[i].last_updated).getTime();
      const to=phase[i+1]
        ? new Date(phase[i+1].last_changed||phase[i+1].last_updated).getTime()
        : cutoff;
      const mins=(Math.min(to,cutoff)-Math.max(from,dayStart.getTime()))/60000;
      if(mins<=0) continue;
      // The tracker counts babySleepState==4 only, i.e. 'sleep'. Stirring is
      // NOT sleep there, so it must not be sleep here either.
      if(st==='sleep') sleep+=mins;
      else if(st==='awake'||st==='stirring') awake+=mins;
    }
    const naps=this._deriveNaps(phase);
    const soothes=(sooth||[]).filter(e=>{
      if(e.state!=='on') return false;
      const t=new Date(e.last_changed||e.last_updated).getTime();
      return t>=dayStart.getTime()&&t<dayEnd.getTime();
    }).length;
    return {
      total_sleep:String(Math.round(sleep)),
      total_awake:String(Math.round(awake)),
      nap_count:String(naps.length),
      longest_nap:String(naps.reduce((m,n)=>Math.max(m,n.durMin),0)),
      soothe_count:String(soothes),
    };
  }

  set hass(hass) {
    this._hass=hass;
    const now=Date.now();
    if(!this._demo && now-this._lastFetch>this._FETCH_INTERVAL){
      this._lastFetch=now; this._loadHistory();
    }
    if(this._statesChanged(hass)) this._scheduleRender();
  }

  async _loadHistory() {
    if(!this._hass||this._demo) return;
    const c=this._config;
    const dayStart=new Date(); dayStart.setHours(0,0,0,0);
    // 8 days: the strip shows 7, and a 0–8am state belongs to the PREVIOUS
    // sleep-day, so one extra day covers the overnight spill. Explicit end_time —
    // without it the API returns only start+1day, which silently reduced the
    // week strip to a single day.
    const histStart=new Date(dayStart); histStart.setDate(histStart.getDate()-7);
    try {
      const weekH=await this._hass.callApi('GET',
        `history/period/${histStart.toISOString()}?filter_entity_id=${this._pick(c.total_sleep)}`
        +`&end_time=${encodeURIComponent(new Date(dayStart.getTime()+86400000).toISOString())}`
        +`&minimal_response=true&no_attributes=true`);
      const weekArr=weekH?.[0]||[];
      // Derive the day map ONCE per fetch — renders must not iterate raw rows.
      this._history={weekArr, byDay:this._byDayTotals(weekArr)};
      this._historyErr=null;
      this._scheduleRender();
    } catch(e){
      // Recorder/history can be unavailable (DB down, history: not enabled).
      // Not fatal — the day timeline falls back to the integration's own
      // nap attributes, so only the week view actually needs history.
      this._history={weekArr:[], byDay:{}};
      this._historyErr=e;
      console.warn('[sleep-card] history unavailable, using live nap attributes',e);
      this._scheduleRender();
    }
  }

  // Sleep-day totals for one calendar month, fetched lazily and cached.
  // Window = [1st 08:00, next 1st 08:00): exactly that month's sleep-days.
  // Past months never change; the current month refreshes every 30 min
  // (today itself comes from the 8-day week fetch, refreshed every 5 min).
  _monthTotals(y,m){
    const key=`${y}-${m}`;
    const hit=this._monthCache[key];
    const now=new Date();
    const isCur=y===now.getFullYear()&&m===now.getMonth();
    if(hit&&(hit.loading||!isCur||Date.now()-hit.at<this._MONTH_FETCH_INTERVAL)) return hit.byDay;
    if(!this._hass||this._demo) return hit?.byDay||{};
    const entry=this._monthCache[key]={loading:true,at:Date.now(),byDay:hit?.byDay||{}};
    const start=new Date(y,m,1,8), end=new Date(y,m+1,1,8);
    if(start>now){entry.loading=false; return entry.byDay;}
    const url=`history/period/${start.toISOString()}?filter_entity_id=${this._pick(this._config.total_sleep)}`
      +`&end_time=${encodeURIComponent((end<now?end:now).toISOString())}`
      +`&minimal_response=true&no_attributes=true`;
    this._hass.callApi('GET',url).then(h=>{
      entry.byDay=this._byDayTotals(h?.[0]||[]); entry.loading=false; entry.at=Date.now();
      this._scheduleRender();
    }).catch(e=>{
      entry.loading=false; entry.at=Date.now();
      console.warn('[sleep-card] month history unavailable',key,e);
    });
    return entry.byDay;
  }

  // Sleep-day total for a date: live week map first (freshest), else month cache.
  _dayTotal(d){
    const k=`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    const w=this._history?.byDay?.[k];
    if(w!=null) return w;
    return this._monthTotals(d.getFullYear(),d.getMonth())[k];
  }

  // Open the detail view (C-chart) on a given date.
  _openDay(date){
    const today=new Date(); today.setHours(0,0,0,0);
    const d=new Date(date); d.setHours(0,0,0,0);
    const diffDays=Math.round((d-today)/86400000);
    if(diffDays>0) return;
    const weekOffset=Math.floor((diffDays+6)/7);
    if(weekOffset<-104) return;
    this._weekOffset=weekOffset;
    this._selectedDayIdx=diffDays+6-weekOffset*7;
    this._showMonthPicker=false; this._cTooltip=null; this._expandedNapIdx=null;
    this._demo=true;
    this._render();
  }

  _render(){if(!this._hass||!this._config) return; this._demo?this._renderPreview():this._renderMain();}

  // Reads a state, collapsing unavailable/unknown/empty to null so the tiles
  // show '–' instead of the literal word "unavailable".
  _st(cand){const e=this._hass?.states[this._pick(cand)];if(!e) return null;const v=e.state;return (v===''||v==null||v==='unavailable'||v==='unknown')?null:v;}
  _attr(cand,key){return this._hass?.states[this._pick(cand)]?.attributes?.[key];}

  _fm(raw){const v=parseFloat(raw);if(isNaN(v)||v<0) return '–';const h=Math.floor(v/60),r=Math.round(v%60);return h===0?`${r}m`:(r===0?`${h}h`:`${h}h ${r}m`);}
  _ft(iso){if(!iso) return '–';try{return new Date(iso).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});}catch{return '–';}}

  // ── Main view ─────────────────────────────────────────────────────────────

  _renderMain(){
    const h=this._hass,c=this._config;
    const phase=this._st(c.sleep_phase)||'unknown';
    const phaseLabel={sleep:'Sleeping',stirring:'Stirring',awake:'Awake',away:'Away',unknown:'–'}[phase]||phase;
    const phaseColor={sleep:'#7b6ed6',stirring:'#f5cc78',awake:'#f9a12d',away:'#aaa',unknown:'#aaa'}[phase]||'#aaa';
    const s={
      sleep:this._st(c.total_sleep), awake:this._st(c.total_awake),
      soothes:this._st(c.soothe_count), naps:this._st(c.nap_count),
      longest:this._st(c.longest_nap),
    };

    // Week bars data
    const weekBars=this._buildWeekBars();
    const maxBar=Math.max(...weekBars.map(b=>b.v),1);
    const monthData=this._mainTab==='month'?this._buildMonthCells():null;

    const barHtml=weekBars.map((b,i)=>{
      const pct=Math.round((b.v/maxBar)*100);
      const h2=b.v>0?Math.max(pct,4):0;
      const col=b.isToday?'#7b6ed6':'#251462';
      const vc=b.isToday?'#7b6ed6':'#4a4a6e';
      return `<div class="bc" data-action="bar" data-bi="${i}" role="button" tabindex="0" title="Open this day">
        <div class="bv" style="color:${vc}">${b.v>0?this._fm(b.v):''}</div>
        <div class="bb" style="height:${h2}%;background:${col}"></div>
        <div class="bl" style="color:${vc}">${b.label}<br>${b.dn}</div>
      </div>`;
    }).join('');

    const isDay=this._mainTab==='day';
    const isWeek=this._mainTab==='week';
    const wFmt=d=>d.toLocaleDateString([],{month:'short',day:'numeric'});
    const weekTitle=this._mainWeekOffset===0?'This week'
      :`${wFmt(weekBars[0].date)} – ${wFmt(weekBars[6].date)}`;

    this.shadowRoot.innerHTML=`<style>
      :host{display:block;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
      .outer{background:${C.bg};border-radius:20px;padding:16px 14px 20px;min-height:calc(100dvh - var(--header-height,56px));box-sizing:border-box}
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
      .wnote{margin-top:14px;text-align:center;font-size:11px;color:${C.textGray}}
      /* Month view */
      .mnav{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
      .mnavb{border:none;background:transparent;font-size:18px;color:${C.textDark};cursor:pointer;padding:6px 14px;font-family:inherit}
      .mnavb:disabled{opacity:.25;cursor:default}
      .mnavt{font-size:16px;font-weight:700;color:${C.textDark}}
      .mgrid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
      .mhead{text-align:center;font-size:11px;font-weight:600;color:${C.textGray};padding:4px 0}
      .mcell{background:rgba(37,20,98,0.05);border-radius:10px;padding:6px 2px;text-align:center;min-height:52px}
      .mcell.mtod{background:rgba(37,20,98,0.13);outline:2px solid #7b6ed6}
      .mcell.mfuture{opacity:.35}
      .mcell[data-action]{cursor:pointer}
      .mcell[data-action]:active,.bc:active{opacity:.6}
      .mdn{font-size:11px;color:${C.textGray};font-weight:600}
      .mval{font-size:11px;font-weight:700;color:#7b6ed6;margin-top:3px}
      .mnote{text-align:center;font-size:11px;color:#bbb;margin-top:10px}
      .ver{text-align:right;font-size:9px;color:#c8c2b8;margin-top:12px}
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
          <div class="sp"><div class="sv">${s.naps??'–'}</div><div class="sl">Naps</div></div>
          <div class="sp"><div class="sv">${s.soothes??'–'}</div><div class="sl">Soothes</div></div>
        </div>
        <div class="sg2">
          <div class="sp"><div class="sv">${this._fm(s.longest)}</div><div class="sl">Longest Nap</div></div>
          <div class="sp"><div class="sv">${this._fm(s.awake)}</div><div class="sl">Awake</div></div>
        </div>
        <canvas id="lc" style="display:block;width:100%"></canvas>
        <div class="leg">
          <div class="li"><div class="lsq" style="background:${C.sleep}"></div>SLEEP</div>
          <div class="li"><div class="lsq" style="background:${C.stirring}"></div>STIRRING</div>
          <div class="li"><div class="lsq" style="background:${C.awake}"></div>AWAKE</div>
          <div class="li"><div class="lsq" style="background:${C.soothe};border-radius:50%"></div>SOOTHES</div>
        </div>
        <button class="pbtn" id="pbtn">Sleep chart &amp; history</button>
      `:''}
      ${isWeek?`
        <div class="mnav">
          <button class="mnavb" data-action="wnav" data-d="-1" ${this._mainWeekOffset<=-104?'disabled':''}>◀</button>
          <div class="mnavt">${weekTitle}</div>
          <button class="mnavb" data-action="wnav" data-d="1" ${this._mainWeekOffset>=0?'disabled':''}>▶</button>
        </div>
        <div class="week-wrap">
          <div class="wbars">${barHtml}</div>
          <div class="wnote">${weekBars.some(b=>b.v>0)?'Tap a day to open its sleep chart.':'No recorded sleep for this week.'}</div>
        </div>
      `:''}
      ${monthData?`
        <div class="mnav">
          <button class="mnavb" data-action="mnav" data-d="-1">◀</button>
          <div class="mnavt">${monthData.mStart.toLocaleString('default',{month:'long',year:'numeric'})}</div>
          <button class="mnavb" data-action="mnav" data-d="1" ${this._monthOffset>=0?'disabled':''}>▶</button>
        </div>
        <div class="mgrid">
          ${['S','M','T','W','T','F','S'].map(l=>`<div class="mhead">${l}</div>`).join('')}
          ${monthData.cells.map(cl=> cl===null?'<div class="mcell"></div>':`
            <div class="mcell${cl.isToday?' mtod':''}${cl.fut?' mfuture':''}"${cl.fut?'':` data-action="mday" data-iso="${cl.iso}" role="button" tabindex="0"`}>
              <div class="mdn">${cl.n}</div>
              <div class="mval">${(!cl.fut&&cl.v>0)?this._fm(cl.v):''}</div>
            </div>`).join('')}
        </div>
        <div class="mnote">Each day = total sleep 8 AM → 8 AM (the crib's sleep-day). Tap a day to open its sleep chart.</div>
      `:''}
      <div class="ver">${CARD_VERSION}</div>
    </div>`;

    this.shadowRoot.getElementById('t-day').addEventListener('click',()=>{this._mainTab='day';this._render();});
    this.shadowRoot.getElementById('t-week').addEventListener('click',()=>{this._mainTab='week';this._render();});
    this.shadowRoot.getElementById('t-month').addEventListener('click',()=>{this._mainTab='month';this._render();});
    this.shadowRoot.querySelectorAll('[data-action="mnav"]').forEach(el=>el.addEventListener('click',()=>{
      if(el.disabled) return;
      this._monthOffset+=parseInt(el.dataset.d)||0;
      if(this._monthOffset>0) this._monthOffset=0;
      this._render();
    }));
    this.shadowRoot.getElementById('pbtn')?.addEventListener('click',()=>{this._demo=true;this._weekOffset=0;this._selectedDayIdx=6;this._render();});

    this.shadowRoot.querySelectorAll('[data-action="wnav"]').forEach(el=>el.addEventListener('click',()=>{
      if(el.disabled) return;
      this._mainWeekOffset=Math.min(0,Math.max(-104,this._mainWeekOffset+(parseInt(el.dataset.d)||0)));
      this._render();
    }));
    // Week bar / month day → that day's detail chart
    this.shadowRoot.querySelectorAll('[data-action="bar"]').forEach(el=>el.addEventListener('click',()=>{
      const b=weekBars[parseInt(el.dataset.bi)]; if(b) this._openDay(b.date);
    }));
    this.shadowRoot.querySelectorAll('[data-action="mday"]').forEach(el=>el.addEventListener('click',()=>{
      const [y,m,d]=el.dataset.iso.split('-').map(Number); this._openDay(new Date(y,m-1,d));
    }));

    // Draw live C-shape
    if(isDay){
      const tl=this._dayTimeline();
      requestAnimationFrame(()=>this._drawC(tl.phase,tl.soothing,'lc',false));
      if(!this._resizeObserver){
        this._resizeObserver=new ResizeObserver(()=>{
          if(!this._demo&&this._mainTab==='day'){
            const t=this._dayTimeline();
            requestAnimationFrame(()=>this._drawC(t.phase,t.soothing,'lc',false));
          }
        });
        this._resizeObserver.observe(this);
      }
    }
  }

  // Today's phase/soothe timeline for the day chart.
  //
  // Recorder history is the richer source (it has stirring sub-phases), but it
  // is not always there. The integration also publishes today's nap sessions
  // and soothe timestamps as attributes on the nap-count sensor, so fall back
  // to those and synthesise the same {state,last_changed} shape _drawC wants.
  // The compact day chart and the detail view share one cache entry, so they
  // can never show different versions of the same day.
  _dayTimeline(){
    const d=this._fetchDay(new Date());
    if((d.phase||[]).length) return {phase:d.phase,soothing:d.soothing||[]};
    return this._napTimeline();
  }

  _byDayTotals(arr){
    // total-sleep history keyed by SLEEP-DAY start (8am–8am, the crib's day):
    // a state at 3am belongs to the sleep-day that started the prior morning.
    // Rows ascend, so the last write per sleep-day = that day's final total.
    // Pure function of the array — call once per fetch and cache the result.
    const byDay={};
    for(const e of arr){
      const d=new Date(e.last_changed||e.last_updated);
      const v=parseFloat(e.state); if(isNaN(v)) continue;
      const sd=new Date(d); if(sd.getHours()<8) sd.setDate(sd.getDate()-1);
      sd.setHours(0,0,0,0);
      const k=`${sd.getFullYear()}-${sd.getMonth()}-${sd.getDate()}`;
      byDay[k]=v;
    }
    return byDay;
  }

  _buildWeekBars(){
    const c=this._config;
    const today=new Date(); today.setHours(0,0,0,0);
    const off=this._mainWeekOffset;
    const DAYS=[];
    for(let i=6;i>=0;i--){
      const d=new Date(today); d.setDate(d.getDate()-i+off*7);
      let v=this._dayTotal(d);
      // Today with no recorder row yet: the live sensor IS today's total.
      if(off===0&&i===0&&v==null){const t=parseFloat(this._st(c.total_sleep)); if(!isNaN(t)) v=t;}
      DAYS.push({date:d,label:d.toLocaleDateString([],{weekday:'short'}).slice(0,2),
        dn:d.getDate(),v:v||0,isToday:off===0&&i===0});
    }
    return DAYS;
  }

  _buildMonthCells(){
    // Calendar grid for the visible month; cell values are sleep-day totals.
    // Prefer the cached 62-day month map; until it lands (or if it failed) fall
    // back to the week map, which still covers the current month's recent days.
    const today=new Date();
    const t0=new Date(today.getFullYear(),today.getMonth(),today.getDate());
    const mStart=new Date(today.getFullYear(), today.getMonth()+this._monthOffset, 1);
    const firstDow=mStart.getDay();
    const dim=new Date(mStart.getFullYear(), mStart.getMonth()+1, 0).getDate();
    const cells=[];
    for(let i=0;i<firstDow;i++) cells.push(null);
    for(let dn=1; dn<=dim; dn++){
      const d=new Date(mStart.getFullYear(), mStart.getMonth(), dn);
      const isToday=d.getTime()===t0.getTime();
      const fut=d.getTime()>t0.getTime();
      cells.push({n:dn, v:fut?null:this._dayTotal(d), fut, isToday, iso:`${d.getFullYear()}-${d.getMonth()+1}-${dn}`});
    }
    return {mStart, cells};
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

    // Real history for the selected day (async; re-renders when it lands)
    const day=this._fetchDay(selDate);
    const phaseHist=day.phase||[];
    const soothHist=day.soothing||[];
    const stats=this._dayStats(phaseHist,soothHist,selDate);
    const hasData=phaseHist.some(e=>e.state==='sleep'||e.state==='stirring'||e.state==='awake');
    const isPastDay=this._dayKey(selDate)!==this._dayKey(new Date());

    const isChart=this._chartTab==='chart';
    const naps=this._dayNaps(phaseHist,selDate);
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

    // Past days keep only the daily totals — the individual nap start/end times
    // live in the tracker, which resets at midnight. So when the totals say the
    // day had naps but the timeline can't itemise them, say that, rather than
    // "no sleep" directly under a summary crediting hours of it.
    const totalNaps=parseInt(stats.nap_count)||0;
    const emptyMsg=day.loading
      ?'Loading…'
      :(totalNaps>0
        ? `${totalNaps} nap${totalNaps===1?'':'s'} totalling ${this._fm(stats.total_sleep)} were recorded this day, but the per-nap times weren't stored.`
        : 'No sleep recorded on this day');
    const listHtml=naps.length===0
      ?`<div class="nonaps">${emptyMsg}</div>`
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
      .outer{background:${C.bg};border-radius:20px;overflow-x:hidden;overflow-y:auto;-webkit-overflow-scrolling:touch;padding-bottom:20px;min-height:calc(100dvh - var(--header-height,56px));box-sizing:border-box;display:flex;flex-direction:column}
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
      .ccard{margin:0 10px 12px;background:#fff;border-radius:16px;box-shadow:0 2px 12px rgba(0,0,0,.07);padding:12px 6px 10px;overflow:hidden;flex:1;min-height:200px;display:flex;flex-direction:column}
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
        <div class="ptitle">${selDate.toLocaleDateString([],{weekday:'long',month:'short',day:'numeric'})}</div>
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
      ${isChart&&!hasData?`
        <div class="ccard" style="align-items:center;justify-content:center">
          <div class="nonaps">${day.loading?'Loading…':(day.error?'History unavailable for this day':emptyMsg)}</div>
        </div>
      `:''}
      ${isChart&&hasData?`
        <div class="ccard">
          <div class="cdl">
            <span>${selDay.letter} ${selDate.toLocaleString('default',{month:'short'}).toUpperCase()} ${selDay.num}, 8 AM</span>
            <span>8 AM</span>
          </div>
          <canvas id="ct" style="display:block;width:100%;cursor:crosshair"></canvas>
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

    if(isChart&&hasData){
      requestAnimationFrame(()=>this._drawC(phaseHist,soothHist,'ct',isPastDay));
      // Tap-to-tooltip on canvas
      const canvas=this.shadowRoot.getElementById('ct');
      if(canvas){
        const handleTap=(e)=>{
          e.preventDefault();
          const rect=canvas.getBoundingClientRect();
          const dpr=window.devicePixelRatio||1;
          // touchend has an EMPTY e.touches; the lifted finger is in changedTouches.
          const pt=e.changedTouches?.[0]||e.touches?.[0]||e;
          const cx2=pt.clientX-rect.left;
          const cy2=pt.clientY-rect.top;
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
          this._drawC(phaseHist,soothHist,'ct',isPastDay);
        };
        canvas.addEventListener('click',handleTap);
        canvas.addEventListener('touchend',handleTap,{passive:false});
      }
      if(!this._resizeObserver){
        this._resizeObserver=new ResizeObserver(()=>{
          if(this._demo&&this._chartTab==='chart'&&hasData) requestAnimationFrame(()=>this._drawC(phaseHist,soothHist,'ct',isPastDay));
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
    // Preview H purely from W (app: startY=W/3, ideal height ~W*4/3).
    // Never read canvas.offsetHeight — stale inline style.height poisons it across re-renders.
    const H=canvasId==='lc'
      ? Math.max(120, Math.min(180, Math.round(W*0.52)))
      : Math.round(W*1.3);
    canvas.width=W*dpr; canvas.height=H*dpr;
    canvas.style.width=W+'px';
    canvas.style.height=H+'px'; // fix display size; .ccard flex:1 fills remaining space in .outer
    const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);

    // Track thickness: W-based so it stays stable regardless of H
    const TK=canvasId==='lc'
      ? Math.max(10, Math.min(28, Math.round(W*0.043)))
      : Math.max(40, Math.min(100, Math.round(W*0.13)));

    // Geometry: topY=W/3 matches app (app uses startY=canvasWidth/3).
    // lc (small day-view canvas): H≈0.52W so topY must use H-basis to keep botY>topY.
    // Main preview canvas: H=1.3W so W/3<H/2 is fine.
    const topY=canvasId==='lc'
      ? Math.max(TK/2+5, Math.round(H*0.22))
      : Math.max(TK/2+10, Math.round(W/3));
    const botY=H-topY;
    const cy=(topY+botY)/2;
    const R=(botY-topY)/2;
    if(R<=0) return; // guard: shouldn't happen after fix, but skip if degenerate
    const lx=TK/2+8;
    const cx=W-R-TK/2-10; // outer arc edge = W-10

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
    ctx.strokeStyle=C.trackBg; ctx.lineWidth=TK+4; ctx.lineCap='round'; ctx.stroke();

    // Phase segments
    const dayStart=new Date();
    if(phaseHist.length>0){const ref=new Date(phaseHist[0].last_changed||phaseHist[0].last_updated);dayStart.setFullYear(ref.getFullYear(),ref.getMonth(),ref.getDate());}
    dayStart.setHours(0,0,0,0);
    // Full 24h for a completed day; stop at "now" for a day still in progress.
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

    if(canvasId!=='lc'){
      // ── Tick marks on inner edge (every 30 min) ──────────────────
      ctx.lineCap='butt';
      for(let h=0;h<=24;h+=0.5){
        const isMajor=(h%1===0);
        const tlen=isMajor?7:4;
        ctx.strokeStyle=isMajor?'rgba(37,20,98,.30)':'rgba(37,20,98,.18)';
        ctx.lineWidth=isMajor?1.5:1;
        if(h<=6){
          const x=lx+(h/6)*(cx-lx), iy=topY+TK/2;
          ctx.beginPath();ctx.moveTo(x,iy);ctx.lineTo(x,iy+tlen);ctx.stroke();
        } else if(h<=18){
          const a=-Math.PI/2+((h-6)/12)*Math.PI;
          const ir=R-TK/2;
          ctx.beginPath();ctx.moveTo(cx+ir*Math.cos(a),cy+ir*Math.sin(a));ctx.lineTo(cx+(ir-tlen)*Math.cos(a),cy+(ir-tlen)*Math.sin(a));ctx.stroke();
        } else {
          const x=cx-((h-18)/6)*(cx-lx), iy=botY-TK/2;
          ctx.beginPath();ctx.moveTo(x,iy);ctx.lineTo(x,iy-tlen);ctx.stroke();
        }
      }

      // ── Time labels on inner edge ─────────────────────────────────
      ctx.fillStyle='#888';
      ctx.font=`bold 9px -apple-system,sans-serif`;
      // Top track: labels below track (inner = bottom of track)
      for(const{t,lbl,al}of[{t:0,lbl:'8 AM',al:'left'},{t:3,lbl:'11 AM',al:'center'},{t:6,lbl:'2 PM',al:'right'}]){
        const x=lx+(t/6)*(cx-lx);
        ctx.textAlign=al; ctx.textBaseline='top';
        ctx.fillText(lbl,t===0?x+2:t===6?x-2:x, topY+TK/2+10);
      }
      // Bottom track: labels above track (inner = top of track)
      for(const{t,lbl,al}of[{t:18,lbl:'2 AM',al:'right'},{t:21,lbl:'5 AM',al:'center'},{t:24,lbl:'8 AM',al:'left'}]){
        const x=cx-((t-18)/6)*(cx-lx);
        ctx.textAlign=al; ctx.textBaseline='bottom';
        ctx.fillText(lbl,t===18?x-2:t===24?x+2:x, botY-TK/2-10);
      }
      // Arc labels: on inner face (facing hollow), right-aligned toward center
      for(const{t,lbl}of[{t:9,lbl:'5 PM'},{t:12,lbl:'8 PM'},{t:15,lbl:'11 PM'}]){
        const a=-Math.PI/2+((t-6)/12)*Math.PI;
        const ir=R-TK/2-14;
        ctx.textAlign='right'; ctx.textBaseline='middle';
        ctx.fillText(lbl,cx+ir*Math.cos(a),cy+ir*Math.sin(a));
      }

      // ── Soothe dots on outer edge with stem ───────────────────────
      const dotR=Math.max(5,Math.round(TK*0.13));
      for(const e of(soothHist||[])){
        if(e.state!=='on') continue;
        const eMs=new Date(e.last_changed||e.last_updated)-dayStart;
        if(eMs<0||eMs>nowMs) continue;
        let t=eMs/3600000-8; if(t<0)t+=24; t=Math.max(0,Math.min(24,t));
        let ox,oy,sx2,sy2; // outer edge point and dot center
        const stemLen=dotR+6;
        if(t<=6){
          ox=lx+(t/6)*(cx-lx); oy=topY-TK/2;
          sx2=ox; sy2=oy-stemLen;
        } else if(t<=18){
          const a=-Math.PI/2+((t-6)/12)*Math.PI;
          const or2=R+TK/2;
          ox=cx+or2*Math.cos(a); oy=cy+or2*Math.sin(a);
          sx2=cx+(or2+stemLen)*Math.cos(a); sy2=cy+(or2+stemLen)*Math.sin(a);
        } else {
          ox=cx-((t-18)/6)*(cx-lx); oy=botY+TK/2;
          sx2=ox; sy2=oy+stemLen;
        }
        // stem line
        ctx.beginPath();ctx.moveTo(ox,oy);ctx.lineTo(sx2,sy2);
        ctx.strokeStyle=C.soothe;ctx.lineWidth=1.5;ctx.lineCap='round';ctx.stroke();
        // dot
        ctx.beginPath();ctx.arc(sx2,sy2,dotR,0,Math.PI*2);
        ctx.fillStyle=C.soothe;ctx.fill();
      }
    } else {
      // Minimal labels for small main canvas
      ctx.fillStyle='#aaa'; ctx.font='8px sans-serif';
      for(const{t,l,al,bl}of[{t:0,l:'8AM',al:'left',bl:'top'},{t:6,l:'2PM',al:'right',bl:'top'},{t:24,l:'8AM',al:'left',bl:'bottom'}]){
        const[px2,py2]=pt(t);
        ctx.textAlign=al;ctx.textBaseline=bl;
        if(t<=6) ctx.fillText(l,t===0?px2+2:px2-2,topY+TK/2+3);
        else ctx.fillText(l,px2+2,botY-TK/2-3);
      }
    }

    // Bed-time start marker (dot on track at first sleep)
    if(canvasId==='ct'){
      const firstIn=phaseHist.find(e=>e.state==='sleep'||e.state==='stirring');
      if(firstIn){
        const eMs=new Date(firstIn.last_changed||firstIn.last_updated)-dayStart;
        let t=eMs/3600000-8; if(t<0)t+=24; t=Math.max(0,Math.min(24,t));
        const[px2,py2]=pt(t);
        ctx.beginPath();ctx.arc(px2,py2,Math.max(6,TK*0.12),0,Math.PI*2);ctx.fillStyle=C.inBed;ctx.fill();
        ctx.beginPath();ctx.arc(px2,py2,Math.max(6,TK*0.12),0,Math.PI*2);ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();
      }
    }

    // ── Tap tooltip ──
    if(this._cTooltip&&canvasId==='ct'){
      const{t:tt,timeStr,stateLabel,stateCol}=this._cTooltip;
      const[px2,py2]=pt(tt);
      ctx.beginPath();ctx.arc(px2,py2,TK/2+3,0,Math.PI*2);ctx.fillStyle='rgba(255,255,255,.25)';ctx.fill();
      ctx.beginPath();ctx.arc(px2,py2,5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();
      ctx.beginPath();ctx.arc(px2,py2,5,0,Math.PI*2);ctx.strokeStyle=stateCol;ctx.lineWidth=2;ctx.stroke();
      // Callout bubble inside the C hollow
      const bW=128,bH=44,bX=lx+2,bY=topY+TK/2+14;
      ctx.beginPath();
      if(ctx.roundRect) ctx.roundRect(bX,bY,bW,bH,8);
      else{const r=8;ctx.moveTo(bX+r,bY);ctx.lineTo(bX+bW-r,bY);ctx.quadraticCurveTo(bX+bW,bY,bX+bW,bY+r);ctx.lineTo(bX+bW,bY+bH-r);ctx.quadraticCurveTo(bX+bW,bY+bH,bX+bW-r,bY+bH);ctx.lineTo(bX+r,bY+bH);ctx.quadraticCurveTo(bX,bY+bH,bX,bY+bH-r);ctx.lineTo(bX,bY+r);ctx.quadraticCurveTo(bX,bY,bX+r,bY);ctx.closePath();}
      ctx.fillStyle='rgba(26,26,46,0.92)';ctx.fill();
      ctx.textAlign='left';ctx.textBaseline='middle';
      ctx.fillStyle='#fff';ctx.font=`bold 11px -apple-system,sans-serif`;ctx.fillText(timeStr,bX+10,bY+14);
      ctx.fillStyle=stateCol;ctx.font=`10px -apple-system,sans-serif`;ctx.fillText(stateLabel,bX+10,bY+30);
      ctx.strokeStyle='rgba(255,255,255,.4)';ctx.lineWidth=1;ctx.setLineDash([3,3]);
      ctx.beginPath();const nearX=Math.min(bX+bW,Math.max(bX,px2));ctx.moveTo(nearX,bY+bH);ctx.lineTo(px2,py2-TK/2-2);ctx.stroke();ctx.setLineDash([]);
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

  // Naps for a past day, using exactly the tracker's rule: a nap runs while
  // babySleepState is 'sleep' and ends at the first non-sleep state (stirring
  // included), with sub-minute blips discarded. Deliberately NOT coalesced —
  // the tracker doesn't coalesce, and stitching here made the same day report
  // 11 naps in one view and 13 in another.
  _deriveNaps(ph){
    if(!ph.length) return [];
    const MIN_NAP_MIN=1;   // matches _MIN_NAP_MINUTES in the integration
    const now=new Date();
    const naps=[];
    let start=null;
    for(const e of ph){
      const isSleep=e.state==='sleep';
      const ts=new Date(e.last_changed||e.last_updated);
      if(isSleep&&start===null) start=ts;
      else if(!isSleep&&start!==null){
        const durMin=Math.round((ts-start)/60000);
        if(durMin>=MIN_NAP_MIN) naps.push({start,end:ts,durMin});
        start=null;
      }
    }
    if(start){
      const durMin=Math.round((now-start)/60000);
      if(durMin>=MIN_NAP_MIN) naps.push({start,end:now,durMin,ongoing:true});
    }
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

  connectedCallback(){
    // Renders no longer ride on every hass update, so tick once a minute to keep
    // time-based bits (now-marker, day rollover) fresh.
    if(!this._tick) this._tick=setInterval(()=>this._scheduleRender(),60_000);
    if(this._hass) this._scheduleRender();
  }
  disconnectedCallback(){
    if(this._tick){clearInterval(this._tick);this._tick=null;}
    if(this._resizeObserver){this._resizeObserver.disconnect();this._resizeObserver=null;}
  }

  static getStubConfig(){return{name:'Avo'};}
}

customElements.define('cradlewise-sleep-card',CradlewiseSleepCard);
window.customCards=window.customCards||[];
if(!window.customCards.find(c=>c.type==='cradlewise-sleep-card'))
  window.customCards.push({type:'cradlewise-sleep-card',name:'Cradlewise Sleep Insights',description:'Interactive C-shape sleep chart. Day/Week tabs, week navigation, tap timeline.',preview:true});
