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
  renderRisk(device, display, fallback);
  unit('volume', display.volume_liters, 'L', 0);
  $('tank-fill').style.height = Math.max(0,Math.min(100,display.fill_percent || 0))+'%';
  $('tank-percent').textContent = display.fill_percent == null ? '—' : number(display.fill_percent,0)+'%';
  $('capacity').textContent = display.capacity_liters == null ? 'Cal configurar les dimensions del dipòsit' : 'de '+number(display.capacity_liters,0)+(display.calibration_mode === 'two_point_prototype' ? ' L nominals · calibratge provisional' : ' L de capacitat calculada');
  $('calibration').textContent = device.calibration_status === 'not_configured' ? 'Sense calibrar' : device.calibration_status === 'out_of_geometry' ? 'Fora de geometria' : device.calibration_mode === 'two_point_prototype' ? 'Prototip · 2 punts' : 'Dipòsit rectangular';
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
  else if (device.calibration_mode === 'two_point_prototype') note += 'Calibratge provisional: 5 cm = 100%, 25 cm = 0%. Litres escalats a la capacitat nominal.' + (device.calibration_clamped ? ' Lectura fora de l’interval; percentatge limitat entre 0 i 100%.' : '');
  else note += 'Volum calculat amb les dimensions configurades.';
  $('reading-note').textContent = note;
  const diagnostics = [['Identificador',device.device_id],['Firmware',device.firmware_version],['Sample ID',device.sample_id],['Temps despert fins al JSON',number(device.awake_ms/1000)+' s'],['Bateria',device.battery_mv == null ? 'No mesurada' : number(device.battery_mv,0)+' mV'],['Topic MQTT',device.topic],['Tipus de dada',device.simulated ? 'Simulada' : 'Sensor']];
  $('diagnostics').replaceChildren();
  for (const [key,value] of diagnostics) { const dt=document.createElement('dt'),dd=document.createElement('dd'); dt.textContent=key;dd.textContent=value;$('diagnostics').append(dt,dd); }
  $('api-link').textContent='/api/v1/devices/'+encodeURIComponent(device.device_id)+'/latest';
}
function renderRisk(device, display, fallback) {
  const percent=display.fill_percent, known=percent != null, risk=known?Math.max(0,Math.min(100,100-percent)):null;
  $('risk-marker').hidden=!known;
  $('risk-meter').classList.toggle('unknown',!known);
  if(!known){$('risk-label').textContent='Sense dades';$('risk-meter').removeAttribute('aria-valuenow');$('risk-meter').setAttribute('aria-valuetext','Sense nivell calibrat');$('risk-note').textContent='Cal una lectura vàlida i calibrada.';return;}
  const label=percent<=10?'Crític':percent<=20?'Alt':percent<=50?'Moderat':'Baix';
  $('risk-label').textContent=label+' · '+number(percent)+'% restant';
  $('risk-marker').style.left=risk+'%';
  $('risk-meter').setAttribute('aria-valuenow',String(risk));
  $('risk-meter').setAttribute('aria-valuetext',label+', '+number(percent)+'% restant');
  $('risk-note').textContent=device.simulated?'Simulació: no genera notificacions.':fallback||device.stale?'Risc segons l’últim nivell vàlid; comprova l’antiguitat de la lectura.':'Risc orientatiu segons el nivell; avisos reforçats al 20% i al 10%.';
}
function renderNotifications(data) {
  $('notification-status').textContent=!data.enabled?'Desactivats':data.last_error?'Reintent pendent':data.pending?'Enviament pendent':'Actius';
  $('notification-status').className='badge'+(data.last_error?' error':data.pending?' warning':'');
  $('notification-summary').textContent='ntfy / '+data.topic+' · '+data.sent+' enviats · '+data.pending+' pendents. '+(data.last_error?'Últim error: '+data.last_error+'. ':'')+'Prioritat alta al 20% i urgent al 10%. Obrir un avís porta a la web final.';
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
    if(selected){renderDevice(devices.find(d=>d.device_id===selected));const history=await api('devices/'+encodeURIComponent(selected)+'/history?hours='+hours+'&limit=500');renderChart(history.readings);renderTable(history.readings);}
    $('updated').textContent='Actualitzat a les '+new Date().toLocaleTimeString('ca-ES')+' · cada 15 s';
  }catch(error){notice(error.message);$('broker-dot').className='dot error';$('broker-status').textContent='Sense dades actualitzades';}
  finally{updating=false;$('refresh').disabled=false;}
}
$('refresh').addEventListener('click',update);
$('device').addEventListener('change',event=>{selected=event.target.value;update();});
for(const button of document.querySelectorAll('[data-hours]'))button.addEventListener('click',()=>{hours=Number(button.dataset.hours);document.querySelectorAll('[data-hours]').forEach(b=>b.classList.toggle('active',b===button));update();});
$('login').addEventListener('submit',event=>{event.preventDefault();token=$('token').value;sessionStorage.setItem('smartgas-token',token);$('token').value='';update();});
update();setInterval(update,15000);
