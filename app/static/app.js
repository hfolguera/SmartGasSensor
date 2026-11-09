'use strict';
const $ = id => document.getElementById(id);
const number = (value, digits = 1) => value == null ? '—' : new Intl.NumberFormat('ca-ES', {maximumFractionDigits: digits}).format(value);
const date = value => new Date(value).toLocaleString('ca-ES', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'});
const states = {ok:'Lectura correcta', reserve:'Nivell de reserva', stale:'Dades antigues', sensor_error:'Error de mesura', calibration_error:'Revisa el calibratge', simulated:'Dades simulades'};
const measurements = {ok:'Correcta', no_echo:'Sense eco', unstable:'Inestable', out_of_range:'Fora de rang', insufficient_samples:'Poques mostres'};
let selected = '', hours = 24, updating = false;
let token = sessionStorage.getItem('smartgas-token') || '';
function age(seconds) {
  if (seconds < 60) return 'Fa ' + seconds + ' s';
  if (seconds < 3600) return 'Fa ' + Math.floor(seconds/60) + ' min';
  if (seconds < 86400) return 'Fa ' + number(seconds/3600) + ' h';
  return 'Fa ' + number(seconds/86400) + ' dies';
}
async function api(path) {
  const response = await fetch('/api/v1/' + path, {headers: token ? {Authorization:'Bearer '+token} : {}, cache:'no-store'});
  if (response.status === 401) { $('login').hidden = false; throw new Error("Introdueix el token per consultar les dades."); }
  if (!response.ok) throw new Error('No s’han pogut recuperar les dades ('+response.status+').');
  $('login').hidden = true;
  return response.json();
}
function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }
function unit(id, value, suffix, digits = 1) {
  const node = $(id); node.replaceChildren(document.createTextNode(number(value,digits)));
  if (value != null) { const span = document.createElement('span'); span.className='unit'; span.textContent=' '+suffix; node.append(span); }
}
function renderDevice(device) {
  $('state').textContent = states[device.state] || device.state;
  $('state').className = 'badge' + (['sensor_error','calibration_error'].includes(device.state) ? ' error' : device.state !== 'ok' ? ' warning' : '');
  const fallback = device.measurement_status !== 'ok' && device.last_valid;
  const display = fallback || device;
  renderAutonomy(device, display, fallback);
  unit('volume', display.volume_liters, 'L', 0);
  $('tank-fill').style.height = Math.max(0,Math.min(100,display.fill_percent || 0))+'%';
  $('tank-percent').textContent = display.fill_percent == null ? '—' : number(display.fill_percent,0)+'%';
  $('capacity').textContent = display.capacity_liters == null ? 'Cal configurar les dimensions del dipòsit' : 'de '+number(display.capacity_liters,0)+(['two_point_prototype','geometry_nominal'].includes(display.calibration_mode) ? ' L nominals · volum estimat' : ' L de capacitat calculada');
  $('calibration').textContent = device.calibration_status === 'not_configured' ? 'Sense calibrar' : device.calibration_status === 'out_of_geometry' ? 'Fora de geometria' : device.calibration_mode === 'two_point_prototype' ? 'Prototip · 2 punts' : device.calibration_mode === 'geometry_nominal' ? 'Dipòsit · capacitat nominal' : 'Dipòsit rectangular';
  $('volume-label').textContent = fallback ? 'ÚLTIM VOLUM VÀLID' : device.calibration_mode === 'two_point_prototype' ? 'VOLUM DEL PROTOTIP' : 'VOLUM ESTIMAT';
  $('reserve').hidden = display.volume_liters == null || display.volume_liters > display.reserve_liters;
  $('reserve').textContent = 'Reserva: '+number(display.reserve_liters,0)+' L';
  unit('distance', display.distance_mm, 'mm');
  $('distance-note').textContent = fallback ? 'Última lectura vàlida · '+age(display.age_seconds) : 'Distància del sensor a la superfície';
  $('last-seen').textContent = age(device.age_seconds);
  $('last-date').textContent = date(device.received_at);
  $('quality').textContent = measurements[device.measurement_status] || device.measurement_status;
  $('samples').textContent = device.valid_samples+' mostres · dispersió '+number(device.spread_mm)+' mm';
  unit('wifi', device.wifi_rssi_dbm, 'dBm', 0);
  $('wifi-note').textContent = device.wifi_rssi_dbm == null ? 'Sense dada' : device.wifi_rssi_dbm >= -60 ? 'Cobertura bona' : device.wifi_rssi_dbm >= -75 ? 'Cobertura moderada' : 'Cobertura feble';
  let note = 'Actualització prevista cada '+number(device.sample_period_s/60)+' min. ';
  if (device.simulated) note += 'SIMULACIÓ: aquesta lectura no prové del sensor real.';
  else if (fallback) note += 'L’últim intent ha fallat. Es mostra una mesura anterior de '+date(display.received_at)+'.';
  else if (device.stale) note += 'La lectura és antiga; no representa necessàriament el nivell actual.';
  else if (device.calibration_status === 'not_configured') note += 'La distància és real; els litres estaran disponibles quan configuris el calibratge.';
  else if (device.calibration_status === 'out_of_geometry') note += 'La distància no és compatible amb les dimensions configurades.';
  else if (device.calibration_mode === 'two_point_prototype') note += 'Calibratge de dos punts: '+number(display.full_distance_mm/10)+' cm = 100%, '+number(display.empty_distance_mm/10)+' cm = 0%. Litres escalats a la capacitat nominal.' + (device.calibration_clamped ? ' Lectura fora de l’interval; percentatge limitat entre 0 i 100%.' : '');
  else if (device.calibration_mode === 'geometry_nominal') note += 'Estimació lineal de '+number(display.capacity_liters,0)+' L nominals: '+number(display.full_distance_mm/10)+' cm = ple i '+number(display.empty_distance_mm/10)+' cm = buit. Dimensions aproximades; pendent de validar al dipòsit.';
  else note += 'Volum calculat amb les dimensions configurades.';
  $('reading-note').textContent = note;
  const diagnostics = [['Identificador',device.device_id],['Firmware',device.firmware_version],['Sample ID',device.sample_id],['Temps despert fins al JSON',number(device.awake_ms/1000)+' s'],['Bateria',device.battery_mv == null ? 'No mesurada' : number(device.battery_mv,0)+' mV'],['Topic MQTT',device.topic],['Tipus de dada',device.simulated ? 'Simulada' : 'Sensor']];
  $('diagnostics').replaceChildren();
  for (const [key,value] of diagnostics) { const dt=document.createElement('dt'),dd=document.createElement('dd'); dt.textContent=key;dd.textContent=value;$('diagnostics').append(dt,dd); }
  $('api-link').textContent='/api/v1/devices/'+encodeURIComponent(device.device_id)+'/latest';
}
function renderAutonomy(device, display, fallback) {
  const percent=display.fill_percent, known=percent != null, autonomy=known?Math.max(0,Math.min(100,percent)):null;
  $('autonomy-marker').hidden=!known;
  $('autonomy-meter').classList.toggle('unknown',!known);
  if(!known){$('autonomy-label').textContent='Sense dades';$('autonomy-meter').removeAttribute('aria-valuenow');$('autonomy-meter').setAttribute('aria-valuetext','Sense nivell calibrat');$('autonomy-note').textContent='Cal una lectura vàlida i calibrada.';return;}
  const label=percent<=10?'Crítica':percent<=20?'Baixa':percent<=50?'Moderada':'Alta';
  $('autonomy-label').textContent=label+' · '+number(percent)+'% restant';
  $('autonomy-marker').style.left=autonomy+'%';
  $('autonomy-meter').setAttribute('aria-valuenow',String(autonomy));
  $('autonomy-meter').setAttribute('aria-valuetext',label+', '+number(percent)+'% restant');
  $('autonomy-note').textContent=device.simulated?'Simulació: no genera notificacions.':fallback||device.stale?'Autonomia segons l’últim nivell vàlid; comprova l’antiguitat de la lectura.':'Orientativa segons el nivell; no és una estimació en dies. Avisos al 20% i al 10%.';
}
function renderNotifications(data) {
  $('notification-status').textContent=!data.enabled?'Desactivats':data.last_error?'Reintent pendent':data.pending?'Enviament pendent':'Actius';
  $('notification-status').className='badge'+(data.last_error?' error':data.pending?' warning':'');
  $('notification-summary').textContent='ntfy / '+data.topic+' · '+data.sent+' enviats · '+data.pending+' pendents. '+(data.last_error?'Últim error: '+data.last_error+'. ':'')+'Prioritat alta al 20% i urgent al 10%. Avisos després de 24 h sense lectures MQTT noves o sense mesures correctes. Obrir un avís porta a la web final.';
  $('notification-events').replaceChildren();
  for(const event of data.events.slice(0,5)){
    const row=document.createElement('div'),content=document.createElement('div'),title=document.createElement('strong'),info=document.createElement('p'),badge=document.createElement('span');
    row.className='notification-event';title.textContent=event.title;info.textContent=date(event.created_at)+' · '+event.device_id+' · '+event.attempts+' intents';badge.className='badge'+(event.priority>=4?' warning':'');badge.textContent={sent:'Enviat',pending:'Pendent',cancelled:'Cancel·lat'}[event.status]||event.status;content.append(title,info);row.append(content,badge);$('notification-events').append(row);
  }
  if(!data.events.length){const text=document.createElement('p');text.className='muted';text.textContent='Encara no hi ha avisos. La primera lectura fixa el nivell de referència; després s’avisa en travessar cada llindar cap avall.';$('notification-events').append(text);}
}
function svgNode(tag,attrs={},text='') { const node=document.createElementNS('http://www.w3.org/2000/svg',tag); for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,value); if(text)node.textContent=text; return node; }
function renderChart(rows) {
  const box=$('chart');box.replaceChildren();
  const volume=rows.some(r=>r.volume_liters != null), field=volume?'volume_liters':'distance_mm', suffix=volume?'L':'mm';
  $('chart-title').textContent=volume?'Volum al llarg del temps':'Distància al llarg del temps';
  const valid=rows.filter(r=>r[field] != null && r.measurement_status === 'ok');
  $('chart-summary').textContent=rows.length+' lectures · '+valid.length+' representades'+(rows.length===500?' · es mostren les 500 més recents':'');
  if(!valid.length){box.textContent='Encara no hi ha lectures vàlides en aquest període.';return;}
  const width=1000,height=230,left=58,right=15,top=16,bottom=34;
  const end=Date.now(),start=end-hours*3600000;
  let low=Math.min(...valid.map(r=>r[field])),high=Math.max(...valid.map(r=>r[field]));
  const padding=Math.max((high-low)*.15,volume?5:1);low=Math.max(0,low-padding);high+=padding;
  const x=t=>left+(t-start)/(end-start)*(width-left-right),y=v=>top+(high-v)/(high-low)*(height-top-bottom);
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':$('chart-title').textContent});
  for(let i=0;i<4;i++){const value=low+(high-low)*i/3,yy=y(value);svg.append(svgNode('line',{x1:left,y1:yy,x2:width-right,y2:yy,stroke:'#e8ede4','stroke-dasharray':'3 4'}),svgNode('text',{x:left-10,y:yy+3,'text-anchor':'end'},number(value,1)+' '+suffix));}
  for(let i=0;i<5;i++){const time=start+(end-start)*i/4;svg.append(svgNode('text',{x:x(time),y:height-5,'text-anchor':i===0?'start':i===4?'end':'middle'},new Date(time).toLocaleString('ca-ES',hours>24?{day:'2-digit',month:'2-digit',hour:'2-digit'}:{hour:'2-digit',minute:'2-digit'})));}
  let path='',previous=null;
  for(const row of rows){if(row[field]==null || row.measurement_status!=='ok'){previous=null;continue;}const time=new Date(row.received_at).getTime();const gap=previous && time-previous.time>Math.max(row.sample_period_s,previous.period)*2500;path+=(previous&&!gap?' L ':' M ')+x(time)+' '+y(row[field]);previous={time,period:row.sample_period_s};}
  svg.append(svgNode('path',{d:path,fill:'none',stroke:'#608a48','stroke-width':2,'stroke-linejoin':'round'}));
  for(const row of valid){const circle=svgNode('circle',{cx:x(new Date(row.received_at).getTime()),cy:y(row[field]),r:valid.length>100?1.8:3,fill:'#608a48'});circle.append(svgNode('title',{},date(row.received_at)+' · '+number(row[field])+' '+suffix+(row.simulated?' · SIMULAT':'')));svg.append(circle);}
  box.append(svg);
}
function renderTable(rows){$('readings').replaceChildren();for(const row of rows.slice(-12).reverse()){const tr=document.createElement('tr');const values=[date(row.received_at),number(row.distance_mm)+' mm',row.volume_liters==null?'—':number(row.volume_liters)+' L',(row.simulated?'SIMULAT · ':'')+(measurements[row.measurement_status]||row.measurement_status),number(row.spread_mm)+' mm',row.wifi_rssi_dbm==null?'—':number(row.wifi_rssi_dbm,0)+' dBm'];for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}$('readings').append(tr);}}
let consumptionRequest=0, consumptionLoadedAt=0, consumptionDevice='';
function shiftYear(value){
  const [y,m,d]=value.split('-').map(Number),lastDay=new Date(Date.UTC(y-1,m,0)).getUTCDate();
  return `${y-1}-${String(m).padStart(2,'0')}-${String(Math.min(d,lastDay)).padStart(2,'0')}`;
}
function dayAfter(value){const d=new Date(value+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10);}
function renderConsumption(data){
  const comparison=data.previous_year, previous=new Map((comparison?.buckets||[]).map(b=>[b.start,b]));
  $('consumption-summary').textContent=data.consumption_liters==null?'Encara no hi ha prou mesures vàlides per calcular el consum d’aquest període.':
    'Consum observat: '+number(data.consumption_liters)+' L · Any anterior: '+(comparison?.consumption_liters==null?'sense dades':number(comparison.consumption_liters)+' L')+' · Cobertura del període: '+number(data.coverage_percent)+'%. Els totals només inclouen intervals observats.';
  $('consumption-method').textContent='Històric fiable des de '+new Date(data.reliable_since).toLocaleString('ca-ES')+'. Hora Europe/Madrid. Mediana horària i filtre de 3 mesures; baixades acumulades de '+number(data.method.noise_liters)+' L i recàrregues a partir de '+number(data.method.refill_threshold_liters)+' L. No es calcula consum sobre buits de més de '+number(data.method.max_gap_seconds/3600)+' h ni entre calibratges diferents. El consum s’aproxima repartint cada baixada entre lectures. Les dades recents són provisionals.'+(data.backfilled_readings?' Les lectures anteriors a aquesta funció s’han convertit amb el calibratge disponible en activar-la.':'');
  const status={complete:'Complet',partial:'Parcial',no_data:'Sense dades',insufficient_data:'Poques dades'};
  $('consumption-rows').replaceChildren();
  for(const bucket of data.buckets){
    const old=previous.get(shiftYear(bucket.start)),tr=document.createElement('tr');
    const label=data.group_by==='month'?bucket.start.slice(0,7):data.group_by==='year'?bucket.start.slice(0,4):bucket.start;
    for(const value of [label,bucket.consumption_liters==null?'—':number(bucket.consumption_liters)+' L',old?.consumption_liters==null?'—':number(old.consumption_liters)+' L',bucket.refill_liters==null?'—':number(bucket.refill_liters)+' L',number(bucket.coverage_percent)+'% / '+number(old?.coverage_percent)+'%',status[bucket.status]]){const td=document.createElement('td');td.textContent=value;tr.append(td);}
    $('consumption-rows').append(tr);
  }
  const box=$('consumption-chart');box.replaceChildren();
  if(!data.buckets.some(b=>b.consumption_liters!=null)&&!comparison?.buckets.some(b=>b.consumption_liters!=null)){box.textContent='El gràfic apareixerà quan hi hagi dades suficients.';return;}
  const width=Math.max(350,box.clientWidth),height=240,left=55,bottom=40,top=18,chartWidth=width-left-20;
  let max=10;for(const bucket of data.buckets){max=Math.max(max,bucket.consumption_liters||0,previous.get(shiftYear(bucket.start))?.consumption_liters||0);}
  max*=1.1;const y=v=>top+(height-top-bottom)*(1-v/max),step=chartWidth/data.buckets.length;
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':'Consum en litres i comparació amb l’any anterior'});
  for(let i=0;i<4;i++){const v=max*i/3;svg.append(svgNode('line',{x1:left,y1:y(v),x2:width-20,y2:y(v),stroke:'#e8ede4'}),svgNode('text',{x:left-8,y:y(v)+4,'text-anchor':'end'},number(v,0)+' L'));}
  data.buckets.forEach((bucket,i)=>{
    const old=previous.get(shiftYear(bucket.start));
    [bucket,old].forEach((entry,j)=>{if(entry?.consumption_liters==null)return;const rect=svgNode('rect',{x:left+i*step+step*(j?.52:.12),y:y(entry.consumption_liters),width:Math.max(.3,step*.33),height:Math.max(1,y(0)-y(entry.consumption_liters)),fill:j?'#ce8d49':'#608a48',opacity:entry.status==='complete'?1:.6});rect.append(svgNode('title',{},entry.start+' · '+number(entry.consumption_liters)+' L · '+status[entry.status]));svg.append(rect);});
    if(i%Math.max(1,Math.ceil(data.buckets.length/Math.max(2,Math.floor(chartWidth/75))))===0){const label=data.group_by==='day'?bucket.start.slice(5):data.group_by==='month'?bucket.start.slice(0,7):bucket.start.slice(0,4);svg.append(svgNode('text',{x:left+(i+.5)*step,y:height-12,'text-anchor':'middle'},label));}
  });box.append(svg);
}
async function loadConsumption(force=false){
  if(!selected||(!force&&consumptionDevice===selected&&Date.now()-consumptionLoadedAt<300000))return;
  if(!$('consumption-start').value||!$('consumption-end').value||$('consumption-end').value<$('consumption-start').value){$('consumption-summary').textContent='Indica una data final igual o posterior a la inicial.';return;}
  const request=++consumptionRequest,device=selected;
  const params=new URLSearchParams({start:$('consumption-start').value,end:dayAfter($('consumption-end').value),group_by:$('consumption-group').value});
  $('consumption-summary').textContent='Carregant el consum…';
  try{const data=await api('devices/'+encodeURIComponent(device)+'/consumption?'+params);if(request!==consumptionRequest||device!==selected)return;renderConsumption(data);consumptionDevice=device;consumptionLoadedAt=Date.now();}
  catch(error){if(request!==consumptionRequest)return;$('consumption-summary').textContent=error.message;$('consumption-chart').replaceChildren();$('consumption-rows').replaceChildren();}
}
const consumptionDate=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Madrid',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(part=>[part.type,part.value]));
const consumptionYear=Number(consumptionDate.year);
$('consumption-year').value=consumptionYear;
function selectConsumptionYear(){const year=Number($('consumption-year').value);if(!Number.isInteger(year)||year<1971||year>2100)return;$('consumption-start').value=year+'-01-01';$('consumption-end').value=year+'-12-31';}
selectConsumptionYear();
$('consumption-start').value=(consumptionYear-1)+'-01-01';
$('consumption-end').value=consumptionDate.year+'-'+consumptionDate.month+'-'+consumptionDate.day;
$('consumption-group').value='month';
$('consumption-year').addEventListener('change',()=>{selectConsumptionYear();loadConsumption(true);});
$('consumption-filters').addEventListener('submit',event=>{event.preventDefault();loadConsumption(true);});
async function update(){
  if(updating)return;updating=true;$('refresh').disabled=true;
  try{
    const [status,data,notifications]=await Promise.all([api('status'),api('devices'),api('notifications')]);
    renderNotifications(notifications);
    const connected=status.mqtt.connected&&status.mqtt.subscribed;
    $('broker-dot').className='dot'+(connected?' online':' error');
    $('broker-status').textContent=!status.mqtt.enabled?'MQTT desactivat':connected?'MQTT connectat':'MQTT desconnectat o sense subscripció';
    const devices=data.devices;$('empty').hidden=devices.length>0;$('dashboard').hidden=!devices.length;
    notice(status.mqtt.storage_errors_total?'Error d’emmagatzematge. Cal revisar els logs i reiniciar l’aplicació.':!connected&&status.mqtt.enabled?'No hi ha connexió activa de subscripció al broker. Les dades desades continuen disponibles.':'');
    const ids=devices.map(d=>d.device_id);if(!ids.includes(selected))selected=ids[0]||'';
    $('device').replaceChildren();for(const device of devices){const option=document.createElement('option');option.value=device.device_id;option.textContent=device.name;option.selected=device.device_id===selected;$('device').append(option);}
    if(selected){renderDevice(devices.find(d=>d.device_id===selected));loadConsumption();const history=await api('devices/'+encodeURIComponent(selected)+'/history?hours='+hours+'&limit=500');renderChart(history.readings);renderTable(history.readings);}
    $('updated').textContent='Actualitzat a les '+new Date().toLocaleTimeString('ca-ES')+' · cada 15 s';
  }catch(error){notice(error.message);$('broker-dot').className='dot error';$('broker-status').textContent='Sense dades actualitzades';}
  finally{updating=false;$('refresh').disabled=false;}
}
$('refresh').addEventListener('click',update);
$('device').addEventListener('change',event=>{selected=event.target.value;update();});
for(const button of document.querySelectorAll('[data-hours]'))button.addEventListener('click',()=>{hours=Number(button.dataset.hours);document.querySelectorAll('[data-hours]').forEach(b=>b.classList.toggle('active',b===button));update();});
$('login').addEventListener('submit',event=>{event.preventDefault();token=$('token').value;sessionStorage.setItem('smartgas-token',token);$('token').value='';update();});
update();setInterval(update,15000);
