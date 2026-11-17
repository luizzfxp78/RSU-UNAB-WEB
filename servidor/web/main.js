/* RSU UNAB WEB — interfaz sin dependencias, consume los endpoints FastAPI originales. */
'use strict';
const API='/api/v1';
const $=(q,ctx=document)=>ctx.querySelector(q);
const $$=(q,ctx=document)=>Array.from(ctx.querySelectorAll(q));
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const trim=v=>String(v??'').trim();
const fmt=v=>v==null||v===''?'—':typeof v==='boolean'?(v?'Sí':'No'):typeof v==='object'?JSON.stringify(v):String(v);
const short=v=>String(v??'').length>48?String(v).slice(0,45)+'…':String(v??'');
const prettify=s=>String(s||'').replace(/[_-]/g,' ').replace(/\b[a-z]/g,l=>l.toUpperCase());
const humanDate=s=>!s?'—':String(s).slice(0,10).split('-').reverse().join('/');
const isId=k=>/(_id|^id$|_base64$|_json$|_url$|^activo$|_actual$)/i.test(k);
const svgIcon=(name)=>{
 const paths={
  home:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
  calls:'<path d="M3 11v2c0 1 1 2 2 2h3l8 4V5l-8 4H5c-1 0-2 1-2 2Z"/><path d="m8 15 2 5"/><path d="M20 9a4 4 0 0 1 0 6"/>',
  projects:'<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 11h18"/>',
  activities:'<path d="M8 3v4M16 3v4M3 10h18M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z"/><path d="m8 15 2 2 5-5"/>',
  volunteers:'<path d="M20.8 4.6a5.4 5.4 0 0 0-7.6 0L12 5.9l-1.2-1.3a5.4 5.4 0 0 0-7.6 7.6L12 21l8.8-8.8a5.4 5.4 0 0 0 0-7.6Z"/>',
  evaluations:'<rect x="4" y="4" width="16" height="17" rx="2"/><path d="M9 3h6v4H9zM8 12l2 2 4-4M8 18h8"/>',
  reports:'<path d="M4 3h11l5 5v13H4z"/><path d="M15 3v5h5M8 16v2M12 13v5M16 11v7"/>',
  hours:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  notifications:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4"/>',
  people:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  roles:'<path d="M8 20v-3a4 4 0 0 0-4-4H2M16 20v-3a4 4 0 0 1 4-4h2"/><circle cx="12" cy="8" r="4"/><path d="M5 8a3 3 0 1 0 0-6M19 8a3 3 0 1 1 0-6"/>',
  catalogs:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l-1.7 1.7a1.7 1.7 0 0 0-1.8-.3l-.9.4-.5 2.4H9.2l-.5-2.4-.9-.4a1.7 1.7 0 0 0-1.8.3l-1.7-1.7a1.7 1.7 0 0 0 .3-1.8l-.4-.9L2 13.6V10l2.2-.5.4-.9a1.7 1.7 0 0 0-.3-1.8L6 5.1a1.7 1.7 0 0 0 1.8.3l.9-.4.5-2.4h5.6l.5 2.4.9.4a1.7 1.7 0 0 0 1.8-.3l1.7 1.7a1.7 1.7 0 0 0-.3 1.8l.4.9L22 10v3.6l-2.2.5Z"/>',
  templates:'<path d="M7 3h9l5 5v13H7zM16 3v5h5M3 7v14M10 13h8M10 17h6"/>',
  audit:'<path d="M5 3h14v18H5zM8 8h8M8 12h8M8 16h5"/>',
  operations:'<path d="M5 7h14l-3-3m3 3-3 3M19 17H5l3-3m-3 3 3 3"/>'
 };return `<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.home}</svg>`;
};
const icon=Object.fromEntries(['home','calls','projects','activities','volunteers','evaluations','reports','hours','notifications','people','roles','catalogs','templates','audit','operations','testlab'].map(x=>[x,svgIcon(x)]));
const MODS=[
 {id:'home',name:'Inicio',sub:'Visión general de responsabilidad social',group:'Principal'},
 {id:'calls',name:'Convocatorias',sub:'Publicación y control de fechas',group:'Gestión RSU'},
 {id:'projects',name:'Proyectos RSU',sub:'Anexos 1 al 7 y certificación',group:'Gestión RSU'},
 {id:'activities',name:'Actividades específicas',sub:'Anexos 8, 9 y 10',group:'Gestión RSU'},
 {id:'volunteers',name:'Voluntariado',sub:'Anexos 11, 12 y 13',group:'Gestión RSU'},
 {id:'evaluations',name:'Evaluaciones',sub:'Seguimiento de expedientes',group:'Seguimiento'},
 {id:'reports',name:'Reportes',sub:'Indicadores y exportación',group:'Seguimiento'},
 {id:'hours',name:'Horas RSU',sub:'Horas registradas y acreditadas',group:'Seguimiento'},
 {id:'notifications',name:'Notificaciones',sub:'Comunicaciones de RSU',group:'Seguimiento'},
 {id:'people',name:'Gestión de personal',sub:'Alumnos, docentes y administrativos',group:'Administración',admin:true},
 {id:'roles',name:'Roles y autoridades',sub:'Asignaciones institucionales',group:'Administración',admin:true},
 {id:'catalogs',name:'Configuración',sub:'Líneas, tipos y departamentos',group:'Administración',admin:true},
 {id:'templates',name:'Plantillas y anexos',sub:'Modelos institucionales',group:'Administración'},
 {id:'audit',name:'Auditoría',sub:'Historial de cambios',group:'Administración',admin:true},
 {id:'operations',name:'Herramientas avanzadas',sub:'Operaciones del servidor',group:'Administración',admin:true},
 {id:'testlab',name:'Pruebas de etapas',sub:'Simulación local de expedientes de ensayo',group:'Administración',admin:true},
];
const MODULE_ENDPOINTS={projects:'/ciclo-rsu/proyectos',activities:'/actividades-especificas',volunteers:'/voluntariados'};
const ENTITY_ACTIONS={
 projects:[
 ['PUT','/ciclo-rsu/proyectos/{project_id}/anexos-1-2','Corregir anexos 1 y 2'],['POST','/ciclo-rsu/proyectos/{project_id}/anexo-3','Presentar Anexo 3'],
 ['POST','/ciclo-rsu/proyectos/{project_id}/anexo-4/evaluar','Evaluar Anexo 4','review'],['POST','/ciclo-rsu/proyectos/{project_id}/acta-resolutiva','Registrar acta resolutiva','admin'],
 ['POST','/ciclo-rsu/proyectos/{project_id}/iniciar-ejecucion','Iniciar ejecución'],['POST','/ciclo-rsu/proyectos/{project_id}/incidencias','Reportar incidencia'],
 ['POST','/ciclo-rsu/proyectos/{project_id}/habilitar-anexo-5','Habilitar informe de avance','admin'],['POST','/ciclo-rsu/proyectos/{project_id}/habilitar-anexo-6','Habilitar informe final','admin'],
 ['POST','/ciclo-rsu/proyectos/{project_id}/anexo-5','Enviar Anexo 5 (avance)'],['POST','/ciclo-rsu/proyectos/{project_id}/anexo-6','Enviar Anexo 6 (final)'],
 ['POST','/ciclo-rsu/proyectos/{project_id}/anexo-7/evaluar','Evaluar Anexo 7','review'],['POST','/ciclo-rsu/proyectos/{project_id}/certificar','Emitir certificados','admin'],
 ['GET','/ciclo-rsu/proyectos/{project_id}/expediente-completo','Descargar expediente','download'],
 ['GET','/ciclo-rsu/proyectos/{project_id}/memorandum','Descargar memorándum','download'],['GET','/ciclo-rsu/proyectos/{project_id}/memorandum-certificacion','Memorándum de certificación','download'],
 ],
 activities:[
 ['PUT','/actividades-especificas/{project_id}/subsanar-plan','Subsanar plan'],['POST','/actividades-especificas/{project_id}/evaluar-plan','Evaluar plan (Anexo 9)','review'],
 ['POST','/actividades-especificas/{project_id}/iniciar-ejecucion','Iniciar ejecución'],['POST','/actividades-especificas/{project_id}/anexo-10','Presentar informe final'],
 ['POST','/actividades-especificas/{project_id}/evaluar-cierre','Evaluar cierre','review'],['POST','/actividades-especificas/{project_id}/certificar','Emitir certificados','admin'],
 ['GET','/actividades-especificas/{project_id}/documentacion','Expediente completo','download'],['GET','/actividades-especificas/{project_id}/memorandums/{kind}','Memorándum','download'],
 ],
 volunteers:[
 ['PUT','/voluntariados/{project_id}/plan','Actualizar plan'],['PUT','/voluntariados/{project_id}/anexo-12','Registro de voluntario (Anexo 12)'],
 ['POST','/voluntariados/{project_id}/presentar','Presentar voluntariado'],['POST','/voluntariados/{project_id}/evaluar-inicial','Evaluar registro inicial','review'],
 ['POST','/voluntariados/{project_id}/iniciar-ejecucion','Iniciar ejecución'],['POST','/voluntariados/{project_id}/anexo-13','Enviar informe final (Anexo 13)'],
 ['POST','/voluntariados/{project_id}/evaluar-final','Evaluar cierre','review'],['POST','/voluntariados/{project_id}/certificar','Emitir certificados','admin'],
 ['GET','/voluntariados/{project_id}/memorandums/{kind}','Memorándum','download'],
 ]
};
const state={token:sessionStorage.getItem('rsu_token')||'',user:null,active:'home',spec:null,lookup:{},context:null,filter:'',dialogCleanup:null};
const ADMIN_ROLES=['administrador_rsu','director_rsu'];
const READER_ROLES=['administrador_rsu','director_rsu','director_facultad'];
const isAdmin=()=>ADMIN_ROLES.includes(trim(state.user?.rol_sistema).toLowerCase()) || (!state.user?.rol_sistema && trim(state.user?.tipo_usuario).toLowerCase()==='administrativo');
const isReviewer=()=>READER_ROLES.includes(trim(state.user?.rol_sistema).toLowerCase());
const isRsuAdministrator=()=>trim(state.user?.rol_sistema).toLowerCase()==='administrador_rsu';
const canCreate=()=>['estudiante','docente'].includes(trim(state.user?.tipo_usuario).toLowerCase());
const btn=(label,command='none',variant='secondary',attrs='')=>`<button type="button" class="btn ${variant}" data-cmd="${esc(command)}" ${attrs}>${esc(label)}</button>`;
const busy=s=>{const r=$('#page-body');if(r)r.innerHTML=`<div class="card loader">${esc(s||'Cargando información…')}</div>`};
function toast(msg,error=false){const host=$('#toast-host');const div=document.createElement('div');div.className='toast'+(error?' error':'');div.textContent=msg;host.append(div);setTimeout(()=>div.remove(),5500)}
function exceptionDetail(detail){if(typeof detail==='string')return detail;if(Array.isArray(detail))return detail.map(x=>{const key=x.loc?.at(-1)||'Campo';const label=labelOf(key);const minimum=x.ctx?.min_length||x.ctx?.minLength||String(x.msg||'').match(/at least (\d+) characters/i)?.[1];if(/min_length|string_too_short|at least .* characters/i.test(x.type+' '+x.msg)&&minimum)return `${label}: escribe por lo menos ${minimum} caracteres.`;return `${label}: ${x.msg}`}).join(' · ');return JSON.stringify(detail||'Error desconocido')}
async function api(path,opt={}){
 const {method='GET',body=null,download=false,multipart=false}=opt;
 const headers={Accept:download?'*/*':'application/json'};
 if(state.token)headers.Authorization=`Bearer ${state.token}`;
 if(body!==null&&!multipart)headers['Content-Type']='application/json';
 let resp;
 try{resp=await fetch(path.startsWith('/api/')?path:API+path,{method,headers,body:body===null?undefined:multipart?body:JSON.stringify(body),cache:'no-store'})}catch(e){throw new Error('No se puede conectar con FastAPI. Verifica que el servidor esté encendido. '+e.message)}
 if(resp.status===401&&state.token){logout(true);throw new Error('Tu sesión expiró. Vuelve a iniciar sesión.')}
 if(!resp.ok){let payload;try{payload=await resp.json()}catch{payload={detail:resp.statusText}}throw new Error(exceptionDetail(payload.detail||payload.message)||`Error ${resp.status}`)}
 if(download){let blob=await resp.blob(),cd=resp.headers.get('Content-Disposition')||'';let match=cd.match(/filename\*=UTF-8''([^;]+)/i)||cd.match(/filename="?([^";]+)"?/i);return {blob,filename:match?decodeURIComponent(match[1]):'documento-rsu.pdf'}}
 if(resp.status===204)return null;
 const txt=await resp.text();if(!txt)return null;try{return JSON.parse(txt)}catch{return txt}
}
function downloadBlob({blob,filename}){const a=document.createElement('a');const link=URL.createObjectURL(blob);a.href=link;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(link),1500)}
async function doDownload(path){try{const result=await api(path,{download:true});downloadBlob(result);toast('Archivo descargado correctamente.')}catch(e){toast(e.message,true)}}
async function login(user,password){const res=await api('/auth/login',{method:'POST',body:{username:user,password}});state.token=res.access_token;sessionStorage.setItem('rsu_token',state.token);state.user=res.user;sessionStorage.setItem('rsu_user',JSON.stringify(state.user));await initialize()}
function logout(silent=false){state.token='';state.user=null;state.spec=null;state.lookup={};sessionStorage.removeItem('rsu_token');sessionStorage.removeItem('rsu_user');closeDialog();renderLogin();if(!silent)toast('Sesión cerrada')}
function renderLogin(){document.title='Acceso | RSU UNAB';$('#root').innerHTML=`<div class="login-layout"><section class="login-hero"><div><img src="/app/assets/logo-unab.png" alt="Logo UNAB"></div><div><div class="eyebrow" style="color:#98e5d7">Universidad Nacional de Barranca</div><h1>Responsabilidad social que transforma comunidades.</h1><p>Gestión integral de convocatorias, proyectos, actividades específicas y voluntariado con trazabilidad en cada etapa.</p></div><small>Plataforma Institucional RSU · UNAB</small></section><section class="login-form-wrap"><form id="login-form" class="login-form"><div class="eyebrow">Bienvenido</div><h2>Iniciar sesión</h2><p>Ingresa con tus credenciales institucionales para acceder a los módulos autorizados.</p><div class="field"><label class="field-label" for="username">Usuario</label><input id="username" class="input" required autocomplete="username" placeholder="Usuario institucional"></div><div class="field"><label class="field-label" for="password">Contraseña</label><input id="password" class="input" required type="password" autocomplete="current-password" placeholder="Tu contraseña"></div><p class="login-error" id="login-error" role="alert"></p><button class="btn block" type="submit">Ingresar a la plataforma →</button><p class="forgot">El acceso y los permisos son administrados por la Dirección de RSU.</p></form></section></div>`;
 $('#login-form').addEventListener('submit',async(e)=>{e.preventDefault();const b=$('#login-form button');b.disabled=true;b.textContent='Verificando acceso…';try{await login($('#username').value,$('#password').value)}catch(err){$('#login-error').textContent=err.message;b.disabled=false;b.textContent='Ingresar a la plataforma →'}})
}
async function initialize(){try{state.spec=await fetch('/openapi.json').then(x=>x.json());}catch(e){toast('No se pudo obtener el catálogo de operaciones del servidor.',true)}renderShell();await loadLookup();await navigate('home');refreshUnread().catch(()=>{});}
function renderShell(){const groups=[...new Set(MODS.map(m=>m.group))], visible=MODS.filter(m=>!m.admin||isAdmin());$('#root').innerHTML=`<div class="layout"><aside class="sidebar"><div class="brand"><img class="brand-logo" src="/app/assets/logo-unab.png" alt="UNAB"><div><strong>RSU UNAB</strong><small>RESPONSABILIDAD SOCIAL</small></div></div><nav>${groups.map(g=>`<div class="nav-group">${esc(g)}</div>${visible.filter(m=>m.group===g).map(m=>`<button class="nav-item" data-nav="${m.id}"><span class="nav-glyph">${icon[m.id]}</span><span>${esc(m.name)}</span>${m.id==='notifications'?'<span id="notif-badge"></span>':''}</button>`).join('')}`).join('')}</nav><div class="sidebar-end">Dirección de Responsabilidad Social Universitaria<br>Universidad Nacional de Barranca · 2026</div></aside><main class="main"><header class="topbar"><div class="top-context"><span class="top-symbol">${svgIcon(state.active)}</span><div><b id="bar-title">Panel general</b><small>Inicio / <span id="bar-crumb">Panel principal</span></small></div></div><div class="top-actions"><button class="btn ghost small" data-nav="notifications">${svgIcon('notifications')} Avisos</button><div class="profile"><div class="avatar">${esc((state.user?.nombre_completo||'U').charAt(0))}</div><div><div class="username">${esc(state.user?.nombre_completo||'Usuario')}</div><div class="role">${esc(prettify(state.user?.rol_sistema||state.user?.tipo_usuario))}</div></div></div><button class="btn ghost small" data-cmd="logout">Salir</button></div></header><section class="content" id="page-body"></section></main></div>`;
 $$('.nav-item').forEach(n=>n.onclick=()=>navigate(n.dataset.nav));
 $('[data-cmd="logout"]').onclick=()=>logout();
 $('[data-nav="notifications"]', $('.top-actions')).onclick=()=>navigate('notifications');
}
function pageHeading(title,sub,actions=''){return `<div class="heading"><div><div class="eyebrow">Responsabilidad Social Universitaria</div><h1>${esc(title)}</h1><p class="sub">${esc(sub)}</p></div><div class="btnset">${actions}</div></div>`}
function moduleBanner(id,title,description,tag='UNAB · RSU'){return `<div class="module-hero"><div class="module-hero-icon">${svgIcon(id)}</div><div><h2>${esc(title)}</h2><p>${esc(description)}</p></div><span class="hero-legislation">${esc(tag)}</span></div>`}
function setPage(html){const x=$('#page-body');if(x)x.innerHTML=html;}
function selectNav(id){$$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.nav===id));$('#bar-title').textContent=MODS.find(x=>x.id===id)?.name||id;const crumb=$('#bar-crumb');if(crumb)crumb.textContent=MODS.find(x=>x.id===id)?.name||id;const symbol=$('.top-symbol');if(symbol)symbol.innerHTML=svgIcon(id);document.title=`${MODS.find(x=>x.id===id)?.name||'RSU'} | UNAB`}
async function navigate(id){if(!MODS.some(m=>m.id===id&&(!m.admin||isAdmin())))id='home';state.active=id;state.filter='';selectNav(id);busy();try{
 if(id==='home')await renderHome();else if(id==='calls')await renderCalls();else if(MODULE_ENDPOINTS[id])await renderEntityList(id);else if(id==='people')await renderPeople();else if(id==='roles')await renderRoles();else if(id==='notifications')await renderNotifications();else if(id==='reports')await renderReport('reportes');else if(id==='evaluations')await renderEvaluationInbox();else if(id==='hours')await renderHours();else if(id==='catalogs')await renderCatalogs();else if(id==='templates')await renderTemplates();else if(id==='audit')await renderAudit();else if(id==='operations')await renderOperations();else if(id==='testlab')await renderTestLab();
 }catch(err){setPage(pageHeading('No se pudo cargar el módulo','El servidor respondió con un error')+`<div class="card empty"><span class="empty-icon">⚠</span><h3>Error al cargar los datos</h3>${esc(err.message)}<p style="margin-top:17px">${btn('Volver a intentar','retry','secondary')}</p></div>`);$('#page-body [data-cmd=retry]').onclick=()=>navigate(state.active)}}
async function refreshUnread(){if(!state.token)return;try{const res=await api('/notificaciones/conteo');const n=Number(res?.no_leidas??res?.unread??res?.total_no_leidas??0);const b=$('#notif-badge');if(b&&n)b.innerHTML=`<span class="pill warn">${n}</span>`;else if(b)b.innerHTML=''}catch{}}
const scopedActions=(arr,context={})=>arr.filter(x=>!x[3]||x[3]!=='admin'||isAdmin()).filter(x=>x[3]!=='review'||isReviewer());
function renderStats(obj){return Object.entries(obj||{}).filter(([k,v])=>typeof v==='number').slice(0,8).map(([k,v],i)=>`<div class="metric card"><div class="metric-ico">${['▣','◎','◷','☑','▦','♧','◈','◉'][i]}</div><div class="value">${Number(v).toLocaleString('es-PE',{maximumFractionDigits:1})}</div><div class="metric-label">${esc(prettify(k))}</div></div>`).join('')}
function empty(label='No hay registros para mostrar'){return `<div class="empty"><span class="empty-icon">▤</span><h3>Sin resultados</h3><p>${esc(label)}</p></div>`}
function statusPill(v){const s=trim(v).toLowerCase();let kind=/aprob|certific|abierta|activo|finaliza|completado|vigente/.test(s)?'good':/observ|pendient|evaluaci|revision|presentado|espera/.test(s)?'warn':/rechaz|cerrad|vencid|inactiv|desaprob/.test(s)?'bad':'';return `<span class="pill ${kind}">${esc(prettify(v||'Sin estado'))}</span>`}
const hideKeys=['password','password_hash','token','firma_base64','datos_json','contenido_base64'];
// Los identificadores UUID permanecen en los objetos para las llamadas a la API.
// Las columnas y fichas muestran nombres descriptivos recuperados de los catálogos permitidos.
const TECH_KEYS=new Set(['password','password_hash','token','firma_base64','datos_json','contenido_base64','jwt','secret_key']);
const TECH_PATTERN=/(^id$|_id$|^uuid$|_uuid$|_base64$|_json$|^hash_|_hash$|^updated_by$)/i;
const UUID_PATTERN=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function refDefinition(key){
 if(refs[key])return refs[key];
 if(/(^|_)(persona_id|person_id|responsable_id|evaluador_id|revisor_id|solicitante_id|asesor_id|docente_id|estudiante_id|voluntario_id|participante_id)$/i.test(key)||/^id_(persona|docente|estudiante|voluntario|responsable)$/i.test(key)||key==='docente_asesor')return ['personas','persona_id',['nombre_completo','dni']];
 if(/(^|_)(proyecto_id|project_id|actividad_id|voluntariado_id)$/i.test(key))return ['expedientes','proyecto_id',['titulo','responsable']];
 if(/convocatoria_id|call_id/i.test(key))return ['convocatorias','convocatoria_id',['tipo_proyecto','periodo_academico']];
 if(/periodo_academico_id/i.test(key))return ['periodos','periodo_academico_id',['nombre']];
 if(/departamento_academico_id/i.test(key))return ['departamentos','departamento_academico_id',['facultad','carrera','nombre']];
 if(/linea_accion_id/i.test(key))return ['lineas','linea_accion_id',['codigo','nombre']];
 if(/tipo_proyecto_id/i.test(key))return ['tipos','tipo_proyecto_id',['nombre']];
 if(/rol_id/i.test(key))return ['rolesproyecto','rol_id',['nombre']];
 return null;
}
function refText(key,value,record={}){
 if(value===null||value===undefined||value==='')return null;
 const def=refDefinition(key);if(!def)return null;
 const [source,id,parts]=def;
 const match=(state.lookup[source]||[]).find(row=>String(row[id])===String(value));
 if(match){const values=parts.map(part=>match[part]).filter(x=>x!==null&&x!==undefined&&x!=='').map(String);return values.join(' · ')||null}
 const candidate={persona_id:['nombre_completo','persona_nombre','nombres_apellidos','nombre','responsable','evaluador_nombre'],proyecto_id:['titulo','nombre_proyecto'],convocatoria_id:['tipo_proyecto','convocatoria_nombre'],periodo_academico_id:['periodo_academico'],tipo_proyecto_id:['tipo_proyecto'],linea_accion_id:['linea_accion'],departamento_academico_id:['facultad']};
 for(const field of candidate[key]||[]){if(record&&typeof record[field]==='string'&&record[field])return record[field]}
 return null;
}
function resolveKnownIdentifier(value){for(const [source,id,parts] of Object.values(refs)){const row=(state.lookup[source]||[]).find(x=>String(x[id])===String(value));if(row)return parts.map(part=>row[part]).filter(Boolean).join(' · ')||'Referencia interna'}return 'Referencia interna'}
function showField(key,value,record={}){
 if(value===null||value===undefined||value==='')return '—';
 if(TECH_KEYS.has(key)||/base64|password|secret|token/i.test(key))return 'Dato protegido';
 if(key==='activo'||key==='leida'||key==='importante')return value?'Sí':'No';
 if(TECH_PATTERN.test(key)||UUID_PATTERN.test(String(value))){return refText(key,value,record)||'Referencia no disponible'}
 if(Array.isArray(value))return value.map(v=>typeof v==='object'?'Elemento registrado':showField(key,v,record)).join(', ');
 if(typeof value==='object')return 'Consultar detalles';
 if(/(^fecha|_fecha|_en$|_at$)/i.test(key)&&/^\d{4}-\d{2}-\d{2}/.test(String(value)))return humanDate(value);
 if(typeof value==='string'&&/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(value))return value.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,x=>resolveKnownIdentifier(x));
 return fmt(value);
}
function displayKey(key){if(TECH_KEYS.has(key)||/base64|password|token|secret/i.test(key))return false;if(TECH_PATTERN.test(key))return Boolean(refDefinition(key));return true}
function displayKeyInRecord(key,row){if(!displayKey(key))return false;if(['proyecto_id','project_id'].includes(key)&&row?.titulo)return false;if((key==='persona_id'||key==='person_id')&&(row?.nombre_completo||row?.persona_nombre))return false;if(key==='convocatoria_id'&&row?.tipo_proyecto&&row?.periodo_academico)return false;return true}
function displayColumns(row,limit=8){return Object.keys(row||{}).filter(k=>displayKeyInRecord(k,row)&&k!=='activo'&&k!=='eliminado_logico').slice(0,limit)}
function colTitle(key){if(TECH_PATTERN.test(key)&&refDefinition(key))return labelOf(key);return labelOf(key)}
function table(rows,opts={}){
 if(!Array.isArray(rows)||!rows.length)return empty();
 const {columns=displayColumns(rows[0]),controls=null}=opts;
 const cols=columns.filter(k=>displayKeyInRecord(k,rows[0]||{}));
 return `<div class="table-scroll"><table class="data-table"><thead><tr>${cols.map(c=>`<th>${esc(colTitle(c))}</th>`).join('')}${controls?'<th>Acciones</th>':''}</tr></thead><tbody>${rows.map((r,i)=>`<tr data-data-row="${i}">${cols.map(c=>{const v=showField(c,r[c],r);return `<td><div class="cut" title="${esc(v)}">${/estado|estatus/i.test(c)?statusPill(v):`<span class="${c==='titulo'||c==='nombre_completo'||c==='nombre'?'strong':''}">${esc(short(v))}</span>`}</div></td>`}).join('')}${controls?`<td class="table-actions">${controls(r,i)}</td>`:''}</tr>`).join('')}</tbody></table></div>`
}
function searchBind(selector, itemsSelector){const input=$(selector);if(!input)return;input.oninput=()=>{const q=input.value.toLowerCase();$$(itemsSelector).forEach(el=>el.hidden=!el.innerText.toLowerCase().includes(q))}}
async function renderHome(){const d=await api('/flujo-rsu/modulos/inicio');const stats=d.counts||{};const modules=d.module_summaries||[];const pending=d.pending||[];const deadlines=d.deadlines||[];const activity=d.quick_indicators||[];
 setPage(pageHeading('Panel de control','Resumen institucional y seguimiento de actividades de RSU',btn('Nueva propuesta','new-proj','secondary')+btn('Ver convocatorias','goto-calls','ghost'))+
 `<div class="metric-grid">${renderStats(stats)}</div><div class="grid-2"><div class="card card-pad"><div class="section-title"><h3>Modalidades RSU</h3><small>Estado por área</small></div><div class="list-rows">${modules.map(m=>`<div class="row"><div><b>${esc(m.title||m.key)}</b><div class="small-k">${esc(m.primary_label||'Total')}: ${esc(m.primary_value)} · ${esc(m.secondary_label||'Pendientes')}: ${esc(m.secondary_value)}</div></div><span class="pill">${esc(m.total??0)} registros</span></div>`).join('')||empty()}</div></div><div class="card card-pad"><div class="section-title"><h3>Accesos rápidos</h3></div><div class="quick-links">${[['projects','Proyectos RSU'],['activities','Actividades específicas'],['volunteers','Voluntariado'],['evaluations','Evaluaciones'],['reports','Reportes'],['notifications','Notificaciones']].map(([id,n])=>`<button class="quick-link" data-shortcut="${id}"><span class="tag-icon">${icon[id]}</span>${esc(n)}</button>`).join('')}</div></div></div><div class="grid-2" style="margin-top:18px"><div class="card card-pad"><div class="section-title"><h3>Expedientes pendientes</h3></div>${Array.isArray(pending)?table(pending,{columns:Object.keys(pending[0]||{}).slice(0,5)}):renderDetails(pending)}</div><div class="card card-pad"><div class="section-title"><h3>Próximos plazos</h3></div>${Array.isArray(deadlines)?(deadlines.length?deadlines.slice(0,8).map(x=>`<div class="activity-entry"><span class="activity-dot">◷</span><div><b>${esc(x.titulo||x.nombre||'Actividad')}</b><p>${esc(x.mensaje||x.estado||x.fecha_limite||x.fecha||'')}</p></div></div>`).join(''):empty('No hay plazos próximos')):renderDetails(deadlines)}</div></div>`);
 $$('[data-shortcut]').forEach(b=>b.onclick=()=>navigate(b.dataset.shortcut));
 $('[data-cmd="new-proj"]').onclick=()=>openAction('POST','/ciclo-rsu/proyectos');$('[data-cmd="goto-calls"]').onclick=()=>navigate('calls')
}
async function renderCalls(){
 const list=await api('/flujo-rsu/convocatorias');state.rows=list;
 state.lookup.convocatorias=list;
 const actions=isAdmin()?btn('+ Nueva convocatoria','call-new','primary'):'';
 const all=list.length,abiertas=list.filter(r=>String(r.estado||'').toLowerCase()==='abierta').length,cerradas=list.filter(r=>String(r.estado||'').toLowerCase()==='cerrada').length;
 const stats={convocatorias_registradas:all,convocatorias_abiertas:abiertas,convocatorias_cerradas:cerradas};
 setPage(pageHeading('Convocatorias','Publicación y seguimiento de convocatorias institucionales',actions)+
 `<div class="module-hero"><div class="module-hero-icon">${svgIcon('calls')}</div><div><h2>Gestiona las convocatorias de RSU</h2><p>Organiza proyectos, actividades específicas y voluntariado según la modalidad y el periodo académico.</p></div><span class="hero-legislation">Reglamento RSU · UNAB</span></div><div class="metric-grid">${renderStats(stats)}</div>`+
 `<div class="card module-table"><div class="list-header"><div><h3>Listado de convocatorias</h3><p>Se muestran nombres de modalidades y periodos, no identificadores de MySQL.</p></div><div class="toolbar"><input class="search" id="search-calls" placeholder="Buscar por modalidad, periodo o estado…"><span class="small-k">${list.length} registros</span></div></div><div id="call-list">${table(list,{columns:['tipo_proyecto','periodo_academico','fecha_inicio','fecha_limite_presentacion','estado'],controls:(r,i)=>btn('Ver',`call-view-${i}`,'ghost small')+(isAdmin()?btn('Editar',`call-edit-${i}`,'secondary small')+btn('Activar',`call-activate-${i}`,'secondary small')+btn('Cerrar',`call-close-${i}`,'warn small'):'')})}</div></div><div class="notice">Las convocatorias nuevas se registran inicialmente como cerradas. La Dirección debe activarlas para recibir propuestas.</div>`);
 searchBind('#search-calls','#call-list tbody tr');
 if(isAdmin())$('[data-cmd=call-new]').onclick=()=>openAction('POST','/flujo-rsu/convocatorias');
 list.forEach((r,i)=>{ $(`[data-cmd="call-view-${i}"]`).onclick=()=>showDetail('Convocatoria de '+(r.tipo_proyecto||'RSU'),r);if(!isAdmin())return;
 $(`[data-cmd="call-edit-${i}"]`).onclick=()=>openAction('PUT','/flujo-rsu/convocatorias/{call_id}',{call_id:r.convocatoria_id},r);
 $(`[data-cmd="call-activate-${i}"]`).onclick=()=>simpleAction('POST',`/flujo-rsu/convocatorias/${encodeURIComponent(r.convocatoria_id)}/activar`,'¿Activar esta convocatoria para recibir propuestas?');
 $(`[data-cmd="call-close-${i}"]`).onclick=()=>simpleAction('POST',`/flujo-rsu/convocatorias/${encodeURIComponent(r.convocatoria_id)}/cerrar`,'¿Cerrar esta convocatoria?')});
}
const ENT={projects:{title:'Proyectos de RSU',sub:'Reglamento: artículo 24 · Anexos 1 a 7 · Mínimo 32 horas',newTitle:'Nuevo proyecto RSU'},activities:{title:'Actividades específicas y de proyección social',sub:'Reglamento: artículos 25–28 · Anexos 8 a 10 · Mínimo 16 horas',newTitle:'Nueva actividad específica'},volunteers:{title:'Voluntariado universitario',sub:'Reglamento: artículos 29–30 · Anexos 11 a 13 · Mínimo 16 horas',newTitle:'Nuevo voluntariado'}};
async function renderEntityList(type){
 const cfg=ENT[type],rows=await api(MODULE_ENDPOINTS[type]);state.rows=rows;
 state.lookup.expedientes=[...(state.lookup.expedientes||[]).filter(old=>!rows.some(row=>row.proyecto_id===old.proyecto_id)),...rows];
 const stateOf=r=>String(r.estado_flujo||r.estado_proyecto||r.estado||'').toLowerCase();
 const total=rows.length,approved=rows.filter(r=>/aprob|certific|finaliza|completad/.test(stateOf(r))).length,observed=rows.filter(r=>/observ|subsan/.test(stateOf(r))).length,pending=rows.filter(r=>/evaluaci|pendient|presentad|revision/.test(stateOf(r))).length;
 const statKeys=type==='projects'?['Proyectos registrados','En evaluación','Observados','Aprobados / cerrados']:type==='activities'?['Actividades registradas','En revisión','Observadas','Aprobadas / cerradas']:['Voluntariados registrados','En revisión','Observados','Aprobados / cerrados'];
 const statObj=Object.fromEntries(statKeys.map((key,i)=>[key,[total,pending,observed,approved][i]]));
 const action=btn('+ '+cfg.newTitle,'new-entity','primary');
 setPage(pageHeading(cfg.title,cfg.sub,action)+
 `<div class="module-hero"><div class="module-hero-icon">${svgIcon(type)}</div><div><h2>${esc(cfg.title)}</h2><p>${type==='projects'?'Registro, evaluación, subsanación, aprobación, seguimiento e informes finales.':type==='activities'?'Planificación, evaluación, desarrollo de actividades e informe final.':'Plan, fichas de voluntarios, seguimiento de jornadas y certificación.'}</p></div><span class="hero-legislation">${type==='projects'?'Anexos 1–7 · 32 horas':type==='activities'?'Anexos 8–10 · 16 horas':'Anexos 11–13 · 16 horas'}</span></div>`+
 `<div class="metric-grid">${renderStats(statObj)}</div><div class="card module-table"><div class="list-header"><div><h3>Listado de ${esc(cfg.title.toLowerCase())}</h3><p>Expedientes y responsables disponibles para tu perfil institucional.</p></div><div class="toolbar"><input class="search" id="entity-search" placeholder="Buscar proyecto, persona, estado…"><select class="input desktop-filter" id="entity-status"><option value="">Todos los estados</option>${[...new Set(rows.map(stateOf).filter(Boolean))].map(st=>`<option value="${esc(st)}">${esc(prettify(st))}</option>`).join('')}</select><span class="small-k">${rows.length} registros</span></div></div><div id="entity-list">${table(rows,{columns:['titulo','responsable','linea_accion','periodo_academico','estado_flujo','etapa_actual'],controls:(r,i)=>btn('Abrir expediente',`entity-open-${i}`,'secondary small')+(type==='projects'?btn('Anexos PDF',`entity-annex-${i}`,'ghost small'):'' )})}</div></div>`);
 function filterRows(){const q=($('#entity-search')?.value||'').toLowerCase(),st=$('#entity-status')?.value||'';$$('#entity-list tbody tr').forEach((tr,i)=>tr.hidden=Boolean((q&&!tr.innerText.toLowerCase().includes(q))||(st&&stateOf(rows[i])!==st)))}
 $('#entity-search').oninput=filterRows;$('#entity-status').onchange=filterRows;
 $('[data-cmd=new-entity]').onclick=()=>openAction('POST',MODULE_ENDPOINTS[type]);
 rows.forEach((r,i)=>{const id=r.proyecto_id;$(`[data-cmd="entity-open-${i}"]`).onclick=()=>openEntity(type,id);if(type==='projects')$(`[data-cmd="entity-annex-${i}"]`).onclick=()=>openAnnexDownloads(type,id)});
}
// Flujo institucional por etapas: el backend sigue siendo la autoridad de permisos y plazos.
const RSU_STAGES={
 projects:['Registro de propuesta','Evaluación inicial','Acto resolutivo','Ejecución','Informe de avance','Evaluación final','Certificación'],
 activities:['Presentación del plan','Evaluación inicial','Ejecución','Evaluación final','Certificación'],
 volunteers:['Plan y fichas','Evaluación inicial','Ejecución','Evaluación final','Certificación']
};
const RSU_ANNEX={
 projects:[[1,'Solicitud'],[2,'Proyecto de RSU'],[3,'Registro de participantes'],[4,'Ficha de evaluación inicial'],[5,'Informe de avance'],[6,'Informe final'],[7,'Evaluación del informe final']],
 activities:[[1,'Solicitud de actividad'],[8,'Plan de actividad'],[9,'Evaluación del plan'],[10,'Informe final']],
 volunteers:[[1,'Solicitud de voluntariado'],[11,'Plan de voluntariado'],[12,'Registro de voluntarios'],[13,'Informe final']]
};
function workflowRoot(type,d){return d?.proyecto||d?.project||d||{}}
function workflowState(type,d){const r=workflowRoot(type,d);return String(r.estado_flujo||r.estado_proyecto||r.estado||'').toLowerCase()}
function workflowStage(type,d){return String(workflowRoot(type,d).etapa_actual||'').toLowerCase()}
function workflowDeadline(type,d){const r=workflowRoot(type,d);return String(r.fecha_limite_subsanacion||r.fecha_limite_actual||'').slice(0,10)}
function workflowIsObserved(type,d){const s=workflowState(type,d);return /observ|subsan/.test(s)}
function workflowReviewPhase(type,d){const stage=workflowStage(type,d),s=workflowState(type,d);if(['evaluacion_final','subsanacion_final'].includes(stage)||/observacion_final|actividad_cierre_presentado|actividad_cierre_observado|voluntariado_en_evaluacion_final|voluntariado_en_convalidacion|voluntariado_observado_final/.test(s))return 'final';return 'initial'}
function workflowStep(type,d){const s=workflowState(type,d),stage=workflowStage(type,d);
 if(type==='projects'){
  if(/certific|finalizado/.test(s)||stage==='certificacion')return 6;
  if(['evaluacion_final','subsanacion_final'].includes(stage)||s==='observacion_final'||s==='cierre')return 5;
  if((d.anexos||{})['5']||s==='monitoreo')return 4;
  if(['ejecucion','inicio_ejecucion'].includes(stage)||/en_ejecucion|listo_ejecucion|memorandum_emitido/.test(s))return 3;
  if(stage==='pendiente_acta'||s==='pendiente_acta')return 2;
  if(['evaluacion_inicial','subsanacion_inicial'].includes(stage))return 1;
  return 0;
 }
 if(type==='activities'){
  if(/certific/.test(s)||stage==='certificacion')return 4;
  if(/cierre/.test(s)||stage==='evaluacion_final')return 3;
  if(/ejecucion|memorandum_emitido/.test(s)||['lista_ejecucion','ejecucion'].includes(stage))return 2;
  if(/evaluaci|observada/.test(s)||stage==='evaluacion_inicial')return 1;
  return 0;
 }
 if(/certific/.test(s)||stage==='certificacion')return 4;
 if(/evaluacion_final|convalidacion|observado_final/.test(s)||stage==='evaluacion_final'||stage==='subsanacion_final')return 3;
 if(/ejecucion|memorandum_inicial/.test(s)||['lista_ejecucion','ejecucion'].includes(stage))return 2;
 if(/evaluacion_inicial|observado_inicial/.test(s)||stage==='evaluacion_inicial'||stage==='subsanacion_inicial')return 1;
 return 0;
}
function workflowStepsHTML(type,d){const at=workflowStep(type,d);return `<div class="workflow-steps" aria-label="Avance del procedimiento">${RSU_STAGES[type].map((label,i)=>`<div class="workflow-step ${i<at?'done':i===at?'current':'locked'}"><span class="workflow-number">${i<at?'✓':i+1}</span><span><b>${esc(label)}</b><small>${i<at?'Etapa completada':i===at?'Etapa actual':'Pendiente'}</small></span></div>`).join('')}</div>`}
function workflowPermission(type,d,permission){if(type==='projects')return Boolean(d.permisos?.[permission]);if(type==='activities')return Boolean(d.permisos?.[permission]);return Boolean(d?.[permission])}
function workflowIsOwner(type,d){return type==='projects'?workflowPermission(type,d,'es_representante'):type==='activities'?workflowPermission(type,d,'es_responsable'):workflowPermission(type,d,'es_responsable')}
function workflowCanEvaluate(type,d){const stage=workflowStage(type,d),p=workflowReviewPhase(type,d);
 if(type==='projects')return Boolean(d.permisos?.puede_revisar)&&['evaluacion_inicial','evaluacion_final'].includes(stage);
 if(type==='activities')return Boolean(d.permisos?.[p==='final'?'puede_evaluar_final':'puede_evaluar_inicial'])&&['evaluacion_inicial','evaluacion_final'].includes(stage);
 return Boolean(d[p==='final'?'puede_evaluar_final':'puede_evaluar_inicial'])&&['evaluacion_inicial','evaluacion_final'].includes(stage);
}
function workflowTimeNotice(type,d){if(!workflowIsObserved(type,d))return '';
 const limit=workflowDeadline(type,d);const today=new Date().toLocaleDateString('en-CA',{timeZone:'America/Lima'});
 const expired=limit&&today>limit;const evals=workflowEvaluationRows(type,d).filter(e=>/observ/.test(String(e.resultado||e.estado||'')));
 const latest=evals[0]||{};const observation=String(latest.observaciones||'').trim();
 return `<div class="workflow-warning ${expired?'expired':''}" role="status"><strong>${expired?'Plazo vencido':'Correcciones solicitadas por RSU'}</strong><p>El responsable dispone de <b>3 días hábiles</b> desde la evaluación para subsanar. ${limit?`Fecha límite: <b>${esc(humanDate(limit))}</b>.`:''} ${expired?'El servidor determinará si admite o archiva la subsanación.':''}</p>${observation?`<p class="workflow-comment">${esc(observation)}</p>`:''}</div>`;
}
function workflowEvaluationRows(type,d){return type==='volunteers'?[...(d.evaluaciones_finales||[]),...(d.evaluaciones_iniciales||[])].map(x=>({...x,resultado:x.estado||x.resultado})):Array.isArray(d.evaluaciones)?[...d.evaluaciones].sort((a,b)=>String(b.fecha_evaluacion||b.fecha_revision||'').localeCompare(String(a.fecha_evaluacion||a.fecha_revision||''))):[]}
// Cada observación se vincula al inciso original de la ficha del evaluador.
// El servidor conserva el historial, los permisos y el plazo reglamentario.
const RSU_INCISO_NAMES={
 linea_accion:'Línea de acción RSU',resumen:'Resumen de la propuesta',problematica:'Problemática a abordar',objetivo:'Objetivo',
 metodologia:'Metodología o estrategias',impacto_social:'Impacto social',impacto_estudiantes:'Impacto en los estudiantes',
 poblacion:'Muestra, población o entorno objetivo',cronograma:'Cronograma de actividades',presupuesto:'Presupuesto',
 plazo_ejecucion:'Plazo de ejecución',lugar_ejecucion:'Lugar de ejecución',medio_difusion:'Medio de difusión',
 financiamiento:'Financiamiento',resumen_evidencias:'Resumen y evidencias',dificultades:'Dificultades',logros:'Resultados y logros',
 recomendaciones:'Recomendaciones',indicadores:'Indicadores',informe_final:'Informe final',
 problemas:'Problemas identificados',necesidades:'Necesidades de los beneficiarios',actividades:'Actividades propuestas',
 beneficiario_institucion:'Institución beneficiaria',beneficiario_facultad:'Facultad beneficiaria',
 beneficiario_escuela:'Escuela beneficiaria',colaboradores:'Colaboradores',
 evidencias_descripcion:'Descripción de evidencias',revision_documental:'Revisión documental'
};
const RSU_CORRECTION_FIELDS={
 linea_accion:['linea_accion_id'],resumen:['resumen','resumen_labores'],problematica:['problematica','problemas'],
 objetivo:['objetivo'],metodologia:['metodologia'],impacto_social:['impacto_social'],impacto_estudiantes:['impacto_estudiantes'],
 poblacion:['poblacion_objetivo','muestra_poblacion_entorno'],cronograma:['cronograma'],presupuesto:['presupuesto'],
 plazo_ejecucion:['fecha_inicio','fecha_fin','horas_totales'],lugar_ejecucion:['lugar_entorno','lugar_ejecucion'],
 medio_difusion:['medio_difusion'],financiamiento:['financiamiento'],resumen_evidencias:['resumen_labores','evidencias_descripcion','evidencias'],
 dificultades:['dificultades'],logros:['resultados_logros','oportunidades_logros'],recomendaciones:['recomendaciones'],
 indicadores:['resultados_logros'],informe_final:['resumen','resumen_labores','evidencias_descripcion'],
 problemas:['problemas'],necesidades:['necesidades'],actividades:['actividades'],
 beneficiario_institucion:['beneficiario_institucion'],beneficiario_facultad:['beneficiario_facultad'],
 beneficiario_escuela:['beneficiario_escuela'],colaboradores:['colaboradores'],
 evidencias_descripcion:['evidencias_descripcion'],revision_documental:[]
};
function rsuIncisoLabel(key){return key?(RSU_INCISO_NAMES[key]||prettify(key)):'Observación general'}
function rsuParseVolunteerNotes(value){const lines=String(value||'').split(/\r?\n/),result=[];
 for(const line of lines){const cleaned=line.trim();if(!cleaned)continue;
  const m=cleaned.match(/^\[INCISO:([a-z0-9_]+)\]\s*(.*)$/i);
  result.push(m?{key:m[1],text:m[2]||'Revisar el apartado indicado.'}:{key:null,text:cleaned});
 }return result;
}
function rsuObservedReviewRows(type,detail,phase){const annex=type==='projects'?(phase==='final'?7:4):type==='activities'?(phase==='final'?10:9):null;
 let reviews=workflowEvaluationRows(type,detail).filter(e=>/observ/i.test(String(e.resultado||e.estado||'')));
 if(type==='volunteers')reviews=reviews.filter(e=>{const initial=(detail.evaluaciones_iniciales||[]).includes(e);return true});
 else reviews=reviews.filter(e=>Number(e.anexo_numero)===annex);
 if(!reviews.length)return [];
 const revs=reviews.map(e=>Number(e.revision_ciclo||e.revision||0));const highest=Math.max(...revs);
 if(highest>0)reviews=reviews.filter(e=>Number(e.revision_ciclo||e.revision||0)===highest);
 return reviews;
}
function rsuCorrectionItems(type,d,phase=workflowReviewPhase(type,d)){
 const seen=new Set(),result=[];
 const put=(key,text,by)=>{text=String(text||'').trim();if(!text)return;
  const marker=String(key||'general')+'|'+text;if(seen.has(marker))return;seen.add(marker);
  result.push({key:key||null,label:rsuIncisoLabel(key),text,by:by||'Comité evaluador',fields:RSU_CORRECTION_FIELDS[key]||[]});
 };
 let reviews=rsuObservedReviewRows(type,d,phase);
 if(type==='volunteers'){
  const specific=phase==='final'?(d.evaluaciones_finales||[]):(d.evaluaciones_iniciales||[]);
  reviews=specific.filter(e=>/observ/i.test(String(e.estado||e.resultado||'')));
  if(reviews.length){const highest=Math.max(...reviews.map(e=>Number(e.revision||0)));if(highest>0)reviews=reviews.filter(e=>Number(e.revision||0)===highest)}
 }
 for(const e of reviews){const by=e.evaluador||e.revisor||'Comité evaluador';const notes=e.observaciones_por_criterio||{};
  if(type==='projects'){
   const criteria=e.criterios||{};
   for(const [key,value] of Object.entries(criteria))if(value===false)put(key,notes[key]||'El evaluador marcó este inciso como «No cumple».',by);
   for(const [key,text] of Object.entries(notes))if(!Object.hasOwn(criteria,key))put(key,text,by);
  }else if(type==='activities'){
   for(const c of (Array.isArray(e.criterios)?e.criterios:[]))if(c.cumple===false)put(c.clave,c.observacion||'El evaluador marcó este inciso como «No cumple».',by);
  }
  if(type==='volunteers'){for(const item of rsuParseVolunteerNotes(e.observaciones))put(item.key,item.text,by)}
  else if(String(e.observaciones||'').trim())put(null,e.observaciones,by);
 }
 return result;
}
function workflowObservations(type,d){const rows=workflowEvaluationRows(type,d).filter(x=>/observ/.test(String(x.resultado||x.estado||'')));
 if(!rows.length)return '';
 const items=rsuCorrectionItems(type,d);
 const latest=items.length?`<div class="rsu-inciso-list">${items.map(item=>`<div class="rsu-inciso-item"><b>${esc(item.key?'Inciso observado: '+item.label:'Observación general')}</b><p>${esc(item.text)}</p><small>${esc(item.by)}</small></div>`).join('')}</div>`:'';
 return `<div class="workflow-observations"><h3>Observaciones del evaluador por inciso</h3><p class="field-help">Cada criterio marcado «No cumple» identifica el dato que debe corregirse antes de reenviar la solicitud.</p>${latest}<details class="rsu-observations-history"><summary>Ver historial completo de evaluaciones (${rows.length})</summary>${rows.slice(0,20).map(x=>{const count=Array.isArray(x.criterios)?x.criterios.filter(c=>c.cumple===false).length:Object.values(x.criterios||{}).filter(v=>v===false).length;return `<article class="workflow-note"><div class="workflow-note-head"><strong>${esc(x.evaluador||x.revisor||'Evaluación institucional')}</strong><small>${esc(humanDate(x.fecha_evaluacion||x.fecha_revision))}</small></div><p>${esc(x.observaciones||'Evaluación por criterios registrados')} · ${count} inciso(s) observado(s)</p></article>`}).join('')}</details></div>`;
}

function workflowButtons(type,d){const actions=[];const state=workflowState(type,d),stage=workflowStage(type,d),owner=workflowIsOwner(type,d),canReview=workflowCanEvaluate(type,d),admin=isAdmin();
 const add=(method,path,label,role='',args={})=>actions.push({method,path,label,role,args});
 if(canReview){add('REVIEW','',workflowReviewPhase(type,d)==='final'?'Evaluar informe final':'Evaluar propuesta','review')}
 if(type==='projects'){
  if(owner&&stage==='registro_anexo3')add('POST','/ciclo-rsu/proyectos/{project_id}/anexo-3','Presentar registro de equipo (Anexo 3)');
  if(owner&&state==='con_observaciones')add('PUT','/ciclo-rsu/proyectos/{project_id}/anexos-1-2','Corregir propuesta y reenviar','correction');
  if(admin&&stage==='pendiente_acta')add('POST','/ciclo-rsu/proyectos/{project_id}/acta-resolutiva','Registrar acto resolutivo');
  if(owner&&stage==='inicio_ejecucion')add('POST','/ciclo-rsu/proyectos/{project_id}/iniciar-ejecucion','Iniciar ejecución');
  if(admin&&stage==='ejecucion'&&!d.anexo6_habilitado_manual&&!d.anexos?.['6'])add('POST','/ciclo-rsu/proyectos/{project_id}/habilitar-anexo-6','Habilitar Anexo 6 anticipadamente');
  const today=new Date().toLocaleDateString('en-CA',{timeZone:'America/Lima'});
  if(owner&&stage==='ejecucion'&&!d.anexos?.['5']&&(d.anexo5_habilitado_manual||!d.fecha_semana_9||String(d.fecha_semana_9).slice(0,10)<=today))add('POST','/ciclo-rsu/proyectos/{project_id}/anexo-5','Presentar informe de avance (Anexo 5)');
  if(owner&&['monitoreo','observacion_final'].includes(state)&&(state==='observacion_final'||d.anexo6_habilitado_manual||!d.fecha_semana_16||String(d.fecha_semana_16).slice(0,10)<=today))add('POST','/ciclo-rsu/proyectos/{project_id}/anexo-6',state==='observacion_final'?'Corregir informe final y reenviar':'Presentar informe final (Anexo 6)',state==='observacion_final'?'correction':'');
  if(admin&&stage==='certificacion')add('POST','/ciclo-rsu/proyectos/{project_id}/certificar','Emitir certificados');
  if(owner&&['inicio_ejecucion','ejecucion'].includes(stage))add('POST','/ciclo-rsu/proyectos/{project_id}/incidencias','Reportar incidencia');
 }else if(type==='activities'){
  if(owner&&state==='actividad_observada')add('PUT','/actividades-especificas/{project_id}/subsanar-plan','Corregir plan y reenviar','correction');
  if(owner&&state==='actividad_memorandum_emitido')add('POST','/actividades-especificas/{project_id}/iniciar-ejecucion','Iniciar ejecución');
  if(owner&&['actividad_en_ejecucion','actividad_cierre_observado'].includes(state))add('POST','/actividades-especificas/{project_id}/anexo-10',state==='actividad_cierre_observado'?'Corregir informe final y reenviar':'Presentar informe final (Anexo 10)',state==='actividad_cierre_observado'?'correction':'');
  if(admin&&state==='actividad_certificacion')add('POST','/actividades-especificas/{project_id}/certificar','Emitir certificados');
 }else{
  if(owner&&['voluntariado_pendiente_fichas','voluntariado_observado_inicial'].includes(state))add('PUT','/voluntariados/{project_id}/plan',state==='voluntariado_observado_inicial'?'Corregir plan de voluntariado':'Actualizar plan',state==='voluntariado_observado_inicial'?'correction':'');
  if(['voluntariado_pendiente_fichas','voluntariado_observado_inicial'].includes(state))add('PUT','/voluntariados/{project_id}/anexo-12','Completar ficha de voluntario (Anexo 12)');
  if(owner&&['voluntariado_pendiente_fichas','voluntariado_observado_inicial'].includes(state))add('POST','/voluntariados/{project_id}/presentar','Enviar a evaluación');
  if(owner&&state==='voluntariado_memorandum_inicial')add('POST','/voluntariados/{project_id}/iniciar-ejecucion','Iniciar ejecución');
  if(owner&&['voluntariado_en_ejecucion_interno','voluntariado_en_ejecucion_externo','voluntariado_observado_final'].includes(state))add('POST','/voluntariados/{project_id}/anexo-13',state==='voluntariado_observado_final'?'Corregir informe final y reenviar':'Presentar informe final (Anexo 13)',state==='voluntariado_observado_final'?'correction':'');
  if(admin&&state==='voluntariado_en_certificacion')add('POST','/voluntariados/{project_id}/certificar','Emitir certificados');
 }
 return actions;
}
// Anexo 5: habilitación institucional solamente en ejecución; simulación aislada en [PRUEBA].
// El backend es la autoridad final del estado y de los permisos, no la interfaz.
function projectAnnex5Availability(detail){
 const p=workflowRoot('projects',detail),stage=workflowStage('projects',detail),status=workflowState('projects',detail);
 if(!isRsuAdministrator())return {mode:'hidden',description:'No estás autorizado a habilitar anexos.'};
 if(detail.anexos?.['5'])return {mode:'completed',description:'El informe de avance (Anexo 5) ya se presentó.'};
 if(detail.anexo5_habilitado_manual||p.anexo5_habilitado_manual)return {mode:'completed',description:'Anexo 5 habilitado. El responsable ya puede presentar su informe de avance.'};
 const sandbox=String(p.titulo||'').trim().toUpperCase().startsWith('[PRUEBA]');
 if(sandbox&&(['evaluacion_inicial','subsanacion_inicial','pendiente_acta','inicio_ejecucion','ejecucion'].includes(stage)||['en_ejecucion','monitoreo'].includes(status)))return {
   mode:'test',description:'Expediente [PRUEBA]: el administrador puede simular la ejecución y habilitar el Anexo 5 sin registrar aprobaciones oficiales.'
 };
 if(stage==='ejecucion'&&['en_ejecucion','monitoreo'].includes(status))return {
   mode:'official',description:'El proyecto está en ejecución. Puedes anticipar la presentación del Anexo 5; se registrará en el historial y se notificará al responsable.'
 };
 return {mode:'locked',description:'Antes de habilitar el Anexo 5 de un proyecto real deben completarse ambas evaluaciones del Anexo 4, emitirse el memorándum, registrarse el acto resolutivo e iniciarse la ejecución.'};
}
function projectAnnex5Control(detail,command){
 if(!isRsuAdministrator())return '';
 const state=projectAnnex5Availability(detail),buttonLabel=state.mode==='test'?'Habilitar Anexo 5 (prueba)':state.mode==='completed'?'Anexo 5 ya habilitado': 'Habilitar Anexo 5';
 const disabled=!['official','test'].includes(state.mode);
 return `<div class="annex5-control ${state.mode==='test'?'annex5-test':''}" role="group" aria-label="Habilitación del Anexo 5"><div><strong>Evaluación inicial · siguiente acción: Anexo 5</strong><p>${esc(state.description)}</p></div>${btn(buttonLabel,command,state.mode==='test'?'warn':'primary',disabled?'disabled aria-disabled="true"':'')}</div>`;
}
async function enableProjectAnnex5(projectId,detail,button){
 const availability=projectAnnex5Availability(detail);
 if(!['official','test'].includes(availability.mode)){toast(availability.description,true);return;}
 const test=availability.mode==='test';
 let url,body;
 if(test){
  const config=await api('/herramientas-prueba/estado');
  if(!config.habilitado){toast('Para usarlo debes activar APP_ENV=local y RSU_ENABLE_TEST_ADVANCE=true en una base aislada de pruebas.',true);return;}
  const motivo=prompt('Motivo de la simulación (mínimo 10 caracteres):','Ensayo del Anexo 5 de responsabilidad social');
  if(motivo===null)return;
  if(motivo.trim().length<10){toast('Escribe un motivo de al menos 10 caracteres.',true);return;}
  if(!confirm('Se habilitará el Anexo 5 del expediente [PRUEBA] sin aprobar oficialmente la evaluación. No permite emitir certificados. ¿Continuar?'))return;
  url=`/herramientas-prueba/expedientes/projects/${encodeURIComponent(projectId)}/habilitar-siguiente`;
  body={motivo:motivo.trim()};
 }else{
  if(!confirm('¿Habilitar anticipadamente el Anexo 5 para que el responsable presente su informe de avance? Se conservarán las aprobaciones y el historial institucional.'))return;
  url=`/ciclo-rsu/proyectos/${encodeURIComponent(projectId)}/habilitar-anexo-5`;
  body={comentario:'Apertura anticipada del informe de avance (Anexo 5) autorizada por administración RSU.'};
 }
 if(button)button.disabled=true;
 try{
  const response=await api(url,{method:'POST',body});
  toast(response.detalle||'Anexo 5 habilitado. El responsable puede ingresar a su expediente para presentar el informe de avance.');
  await openEntity('projects',projectId);
  refreshUnread().catch(()=>{});
 }catch(e){toast(e.message,true);if(button)button.disabled=false;}
}
function workflowActionInitial(type,d,path){const p=workflowRoot(type,d);
 if(type==='projects'){
  if(path.includes('/anexos-1-2')){
   const saved=d.anexos?.['2']?.contenido||{};
   return {...saved,convocatoria_id:saved.convocatoria_id||p.convocatoria_id,linea_accion_id:saved.linea_accion_id||p.linea_accion_id,titulo:saved.titulo||p.titulo,
   miembros:Array.isArray(saved.miembros)?saved.miembros:[],firmas:(d.archivos||[]).filter(f=>f.proposito==='firma'&&f.workflow_file_id).map(f=>({workflow_file_id:f.workflow_file_id,filename:f.nombre_archivo,label:f.etiqueta||'Firma del expediente',person_id:f.persona_firmante_id||null}))};
  }
  if(path.includes('/anexo-3'))return {titulo:p.titulo,linea_accion_id:p.linea_accion_id,miembros:(d.equipo||[]).map(m=>({persona_id:m.persona_id,role:m.rol||m.role||'colaborador'}))};
  if(path.includes('/anexo-6'))return d.anexos?.['6']?.contenido||{};
  return {};
 }
 if(type==='activities'){
  if(path.endsWith('/subsanar-plan')){
   const plan=p.anexo_8||{};
   return {...plan, titulo:p.titulo,linea_accion_id:p.linea_accion_id,fecha_inicio:p.fecha_inicio,fecha_fin:p.fecha_fin,horas_totales:p.horas_totales,
    miembros:(p.equipo||[]).filter(m=>m.rol!=='responsable').map(m=>({persona_id:m.persona_id,rol:m.rol})),firmas:(p.firmas||[]).map(f=>({workflow_file_id:f.workflow_file_id,filename:f.nombre_archivo||'firma.png',label:f.etiqueta||'Firma del expediente',person_id:f.persona_firmante_id||null}))};
  }
  if(path.endsWith('/anexo-10'))return {...(p.anexo_10||p.anexo_8||{}),fecha_inicio:p.fecha_inicio,fecha_fin:p.fecha_fin,horas_totales:p.horas_totales};
 }
 if(type==='volunteers'){
  if(path.endsWith('/plan')){const plan=d.anexo_11?.contenido||{};return {...plan,titulo:p.titulo,linea_accion_id:p.linea_accion_id,fecha_inicio:p.fecha_inicio,fecha_fin:p.fecha_fin,
   colaboradores:(d.colaboradores||[]).map(m=>({persona_id:m.persona_id}))};}
  if(path.endsWith('/anexo-13'))return d.anexo_13?.contenido||{};
 }
 return {};
}
function workflowAnnexAvailable(type,d,n){const p=workflowRoot(type,d);
 if(type==='projects'){
  if([4,7].includes(n))return (d.evaluaciones||[]).some(e=>Number(e.anexo_numero)===n);
  return Boolean(d.anexos&&d.anexos[String(n)]);
 }
 if(type==='activities')return n===9?(d.evaluaciones||[]).some(e=>Number(e.anexo_numero)===9):Number(p[`anexo_${n}_revision`]||0)>0;
 if(n===1)return Boolean(d.anexo_1);if(n===11)return Boolean(d.anexo_11);if(n===12)return Boolean(d.anexo_12_consolidado);if(n===13)return Boolean(d.anexo_13);
 return false;
}
function workflowDownloadsHTML(type,d){const p=workflowRoot(type,d);
 const items=RSU_ANNEX[type].map(([n,label])=>({n,label,ready:workflowAnnexAvailable(type,d,n),kind:'annex'}));
 if(type==='projects'){
  items.push({n:'memo',label:'Memorándum de aprobación',ready:!!d.memorandum,kind:'memo'});
  items.push({n:'memo-final',label:'Memorándum de aprobación final',ready:!!d.memorandum_certificacion,kind:'memo-final'});
 }else{
  for(const m of (type==='activities'?p.memorandums||[]:d.memorandums||[])){
   const typeMemo=String(m.tipo||'');const isFinal=/final/.test(typeMemo);items.push({n:isFinal?'memo-final':'memo',label:isFinal?'Memorándum de aprobación final':'Memorándum de aprobación',ready:true,kind:isFinal?'memo-final':'memo'});
  }
 }
 const certificates=type==='projects'?d.certificados||[]:type==='activities'?p.certificados||[]:d.certificados||[];
 for(const [i,c] of certificates.entries())items.push({n:i,label:'Certificado · '+(c.nombre_completo||c.participante||'Participante'),ready:!!c.certificado_id,kind:'cert',id:c.certificado_id});
 return `<div class="workflow-files">${items.map(item=>`<div class="workflow-file ${item.ready?'available':'unavailable'}"><span class="workflow-file-icon">${item.ready?'▣':'🔒'}</span><div><strong>${item.kind==='annex'?'Anexo '+item.n+' · ':''}${esc(item.label)}</strong><small>${item.ready?'Disponible para descarga':'Se habilita después de completar la etapa correspondiente'}</small></div><button class="btn ${item.ready?'secondary':'ghost'} small" data-file-kind="${esc(item.kind)}" data-file-number="${esc(item.n)}" ${item.id?`data-cert-id="${esc(item.id)}"`:''} ${item.ready?'':'disabled'}>${item.ready?'Descargar':'Bloqueado'}</button></div>`).join('')}</div>`;
}
function workflowDownloadPath(type,id,kind,n,certId){const root=MODULE_ENDPOINTS[type]+'/'+encodeURIComponent(id);
 if(kind==='annex')return `${root}/anexos/${n}/pdf`;
 if(kind==='cert')return type==='projects'?`/ciclo-rsu/certificados/${encodeURIComponent(certId)}/descargar`:`${MODULE_ENDPOINTS[type]}/certificados/${encodeURIComponent(certId)}/descargar`;
 if(type==='projects')return `${root}/${kind==='memo'?'memorandum':'memorandum-certificacion'}`;
 if(type==='activities')return `${root}/memorandums/${kind==='memo'?'aprobacion':'final'}`;
 return `${root}/memorandums/${kind==='memo'?'inicial':'final'}`;
}
async function openEntity(type,id){
 state.context={type,id};const d=await api(`${MODULE_ENDPOINTS[type]}/${encodeURIComponent(id)}`);const p=workflowRoot(type,d);const stateText=workflowState(type,d);const actions=workflowButtons(type,d);
 const files=workflowDownloadsHTML(type,d);const history=workflowObservations(type,d);
 const detailData=type==='projects'?{datos:p.titulo,estado:p.estado_flujo,responsable:(d.equipo||[]).find(x=>(x.rol||x.role)==='responsable')?.nombre_completo||p.responsable,
   linea_accion:p.linea_accion,periodo_academico:p.periodo_academico,fecha_inicio:p.fecha_inicio,fecha_fin:p.fecha_fin,equipo:d.equipo,historial:d.historial}:
 type==='activities'?{titulo:p.titulo,responsable:(p.equipo||[]).find(x=>x.rol==='responsable')?.nombre_completo||p.responsable,linea_accion:p.linea_accion,periodo_academico:p.periodo_academico,equipo:p.equipo,fecha_inicio:p.fecha_inicio,fecha_fin:p.fecha_fin}:
 {titulo:p.titulo,responsable:d.responsable?.nombre_completo||p.responsable,linea_accion:p.linea_accion,colaboradores:d.colaboradores,fecha_inicio:p.fecha_inicio,fecha_fin:p.fecha_fin};
 const progress=workflowStepsHTML(type,d),warning=workflowTimeNotice(type,d);const stateProgress=`<div class="stage-confirmation"><strong>Etapa actual: ${esc(prettify(workflowStage(type,d)||workflowState(type,d)))}</strong><p>Las etapas con ✓ representan el avance del flujo registrado. Los próximos anexos se habilitan según el estado de la solicitud.</p>${String(p.titulo||'').toUpperCase().startsWith('[PRUEBA]')?'<p class="notice" role="status"><strong>Expediente de PRUEBA:</strong> este avance puede incluir simulaciones, no equivale a aprobación oficial y no habilita certificados institucionales.</p>':''}</div>`;const testButton=isAdmin()&&String(p.titulo||'').toUpperCase().startsWith('[PRUEBA]')?`<div class="workflow-section test-box" id="test-advance-container"><div><strong>Zona de pruebas del administrador RSU</strong><p>Habilitación de etapas solo para expedientes [PRUEBA]. No genera aprobaciones oficiales.</p></div>${btn('Habilitar siguiente documento (prueba)','test-next-stage','warn')}</div>`:'';
 const intro=stateText?statusPill(stateText):'<span class="small-k">Pendiente</span>';
 const body=`<div class="workflow-entry"><div class="workflow-heading"><div><div class="small-k">${esc(ENT[type].title)} · Procedimiento institucional UNAB</div><h3>${esc(p.titulo||'Expediente')}</h3></div>${intro}</div>${progress}${stateProgress}${warning}
 <div class="workflow-section"><div class="section-title"><h3>${workflowCanEvaluate(type,d)?'Revisión asignada a tu usuario':'Siguiente paso del procedimiento'}</h3></div><p class="sub">Las acciones dependen de la etapa, los permisos y la revisión por las autoridades correspondientes.</p><div class="workflow-actions">${actions.map((a,i)=>btn(a.label,`workflow-action-${i}`,a.role==='review'?'primary':'secondary')).join('')||'<div class="hint">El expediente espera la actuación de otra autoridad o la siguiente fecha reglamentaria. No hay acciones disponibles para tu usuario en esta etapa.</div>'}</div></div>
 ${type==='projects'?projectAnnex5Control(d,'enable-annex5-detail'):''}
 <div class="workflow-section"><div class="section-title"><h3>Archivos y anexos habilitados</h3><span class="small-k">Se desbloquean conforme avanza el expediente</span></div>${files}</div>
 ${history?`<div class="workflow-section">${history}</div>`:''}${testButton}
 <div class="workflow-section"><div class="section-title"><h3>Información del expediente</h3>${btn('Actualizar','workflow-refresh','ghost small')}</div>${renderDetails(detailData)}</div></div>`;
 openDialog(p.titulo||'Expediente RSU',body,btn('Volver al listado','dialog-back','ghost'),()=>{closeDialog();navigate(type).catch(e=>toast(e.message,true))});
 actions.forEach((a,i)=>{$(`[data-cmd="workflow-action-${i}"]`,$('#dialog-host')).onclick=()=>{
  if(a.method==='REVIEW')return openReviewForm(type,id,d,workflowReviewPhase(type,d));
  const current=workflowActionInitial(type,d,a.path);
  return openAction(a.method,a.path,{project_id:id},current,'',()=>openEntity(type,id),a.role==='correction'?rsuCorrectionItems(type,d,workflowReviewPhase(type,d)):[]);
 }});
 $$('[data-file-kind]',$('#dialog-host')).forEach(b=>{if(!b.disabled)b.onclick=()=>doDownload(workflowDownloadPath(type,id,b.dataset.fileKind,b.dataset.fileNumber,b.dataset.certId))});
 const enableAnnex5=$('[data-cmd=enable-annex5-detail]',$('#dialog-host'));
 if(enableAnnex5&&!enableAnnex5.disabled)enableAnnex5.onclick=()=>enableProjectAnnex5(id,d,enableAnnex5);
 $('[data-cmd=workflow-refresh]',$('#dialog-host')).onclick=()=>openEntity(type,id);
 const testNext=$('[data-cmd=test-next-stage]',$('#dialog-host'));if(testNext)testNext.onclick=async()=>{try{const setting=await api('/herramientas-prueba/estado');if(!setting.habilitado)throw new Error('Activa RSU_ENABLE_TEST_ADVANCE=true y APP_ENV=local en .env, solo para ensayos locales.');const motivo=prompt('Motivo de la simulación (mínimo 10 caracteres):','Ensayo de flujo y anexos RSU');if(!motivo)return;if(!confirm('Este atajo ALTERA el estado del expediente [PRUEBA] para ensayos. No registra aprobaciones ni certificaciones. ¿Continuar?'))return;testNext.disabled=true;const response=await api(`/herramientas-prueba/expedientes/${type}/${encodeURIComponent(id)}/habilitar-siguiente`,{method:'POST',body:{motivo}});toast(response.detalle||'Etapa de prueba habilitada.');await openEntity(type,id)}catch(e){toast(e.message,true);testNext.disabled=false}};
}
async function openAnnexDownloads(type,id){const d=await api(`${MODULE_ENDPOINTS[type]}/${encodeURIComponent(id)}`);openDialog('Documentos habilitados',`<div class="hint">Se muestran exclusivamente los documentos emitidos o presentados en el procedimiento institucional.</div>${workflowDownloadsHTML(type,d)}`,btn('Volver al expediente','dialog-back','ghost'),()=>openEntity(type,id));$$('[data-file-kind]',$('#dialog-host')).forEach(b=>{if(!b.disabled)b.onclick=()=>doDownload(workflowDownloadPath(type,id,b.dataset.fileKind,b.dataset.fileNumber,b.dataset.certId))});}
// Cada criterio se muestra junto a los datos que realmente presentó el responsable.
function reviewContent(type,detail,phase){const p=workflowRoot(type,detail);
 if(type==='projects')return phase==='final'?(detail.anexos?.['6']?.contenido||{}):(detail.anexos?.['2']?.contenido||{});
 if(type==='activities')return phase==='final'?(p.anexo_10||{}):(p.anexo_8||{});
 return phase==='final'?(detail.anexo_13?.contenido||{}):(detail.anexo_11?.contenido||{});
}
function reviewCriterionValue(type,phase,key,detail){const p=workflowRoot(type,detail),c=reviewContent(type,detail,phase);
 const candidates={
  linea_accion:[p.linea_accion,c.linea_accion,c.linea_accion_id],
  resumen:[c.resumen,c.resumen_labores,c.actividades],problematica:[c.problematica,c.problemas],
  objetivo:[c.objetivo,c.objetivos],metodologia:[c.metodologia,c.estrategias],
  impacto_social:[c.impacto_social],impacto_estudiantes:[c.impacto_estudiantes],
  poblacion:[c.poblacion_objetivo,c.muestra_poblacion_entorno],
  cronograma:[c.cronograma,c.actividades_semanales],
  presupuesto:[c.presupuesto,c.financiamiento],
  plazo_ejecucion:[`Inicio: ${humanDate(p.fecha_inicio)} · Fin: ${humanDate(p.fecha_fin)} · Horas: ${p.horas_totales||'—'}`],
  lugar_ejecucion:[c.lugar_entorno,c.lugar_ejecucion,p.lugar_ejecucion],
  medio_difusion:[c.medio_difusion],financiamiento:[c.financiamiento],
  resumen_evidencias:[c.resumen_labores,c.evidencias_descripcion,c.evidencias],
  dificultades:[c.dificultades],logros:[c.resultados_logros,c.oportunidades_logros],
  recomendaciones:[c.recomendaciones],indicadores:[c.indicadores,c.resultados_logros],
  informe_final:[c.resumen,c.problematica,c.objetivo,c.metodologia,c.impacto_social,c.evidencias_descripcion],
 };
 const first=(candidates[key]||[c[key]]).find(x=>x!==undefined&&x!==null&&x!=='');
 if(first===undefined)return '<span class="evidence-missing">El responsable no registró información para este criterio.</span>';
 if(typeof first==='object'){
  const json=Array.isArray(first)?first.map(x=>typeof x==='object'?Object.values(x).filter(Boolean).join(' · '):x).join(' / '):Object.entries(first).map(([k,v])=>`${prettify(k)}: ${typeof v==='object'?JSON.stringify(v):v}`).join(' / ');
  return `<div class="evidence-text">${esc(json.slice(0,1600)||'Sin contenido')}</div>`;
 }
 return `<div class="evidence-text">${esc(String(first).slice(0,1800))}</div>`;
}
function reviewSummaryHTML(type,detail,phase){const p=workflowRoot(type,detail),c=reviewContent(type,detail,phase);
 const selected=[['Título',p.titulo],['Responsable',(detail.equipo||p.equipo||[]).find(x=>String(x.rol||x.role).toLowerCase()==='responsable')?.nombre_completo||detail.responsable?.nombre_completo||p.responsable],['Línea de acción',p.linea_accion],['Lugar',p.lugar_ejecucion||c.lugar_entorno],['Periodo',p.periodo_academico]];
 return `<section class="review-project-header"><h3>Información presentada por el responsable</h3><div class="review-summary">${selected.map(([k,v])=>`<div><small>${esc(k)}</small><strong>${esc(v||'No indicado')}</strong></div>`).join('')}</div></section>`;
}
async function openReviewForm(type,id,detail,phase='initial'){
 if(!workflowCanEvaluate(type,detail)){toast('Tu usuario no tiene una evaluación pendiente asignada en esta etapa.',true);return}
 const path=type==='projects'?`/ciclo-rsu/proyectos/${encodeURIComponent(id)}/anexo-${phase==='final'?7:4}/evaluar`:
 type==='activities'?`/actividades-especificas/${encodeURIComponent(id)}/${phase==='final'?'evaluar-cierre':'evaluar-plan'}`:
 `/voluntariados/${encodeURIComponent(id)}/${phase==='final'?'evaluar-final':'evaluar-inicial'}`;
 const title=phase==='final'?'Evaluación del informe final':'Evaluación de la propuesta';
 let criteria=[];
 if(type==='projects'){
  const catalog=await api('/ciclo-rsu/catalogo/criterios');criteria=Object.entries(catalog[phase==='final'?'anexo_7':'anexo_4']||{}).map(([key,text])=>({key,text}));
 }else if(type==='activities'){
  const catalog=await api('/actividades-especificas/catalogo/criterios');criteria=(catalog[phase==='final'?'final':'inicial']||[]).map(c=>({key:c.clave,text:c.nombre}));
 }
 let criteriaHTML=criteria.map((c,i)=>`<div class="workflow-criterion" data-criterion="${esc(c.key)}"><div class="workflow-criterion-grid"><div class="criterion-evidence"><b>${i+1}. ${esc(c.text)}</b><small>Información del expediente</small>${reviewCriterionValue(type,phase,c.key,detail)}</div><div class="criterion-decision"><label class="field-label">Decisión del evaluador</label><select class="input criterion-choice" aria-label="Evaluación de ${esc(c.text)}"><option value="true">Sí · Cumple</option><option value="false">No · Observar</option></select><textarea class="input criterion-note" rows="2" placeholder="Indica la corrección requerida" hidden></textarea></div></div></div>`).join('');
 const annex5InReview=type==='projects'&&phase==='initial'?projectAnnex5Control(detail,'enable-annex5-review'):'';
 const volunteerIncisos=phase==='initial'?[['linea_accion','Línea de acción'],['plazo_ejecucion','Fechas del plan'],['beneficiario_institucion','Institución beneficiaria'],['problemas','Problemas'],['necesidades','Necesidades'],['objetivo','Objetivo'],['actividades','Actividades'],['colaboradores','Colaboradores'],['revision_documental','Ficha del voluntario (Anexo 12)']]:[['plazo_ejecucion','Fechas y horas'],['actividades','Actividades realizadas'],['evidencias_descripcion','Evidencias'],['revision_documental','Informe final y anexos']];
 const volunteerFieldHTML=type==='volunteers'?`<div id="volunteer-observed-fields" class="rsu-volunteer-observations" hidden><h3>¿Qué incisos debe corregir el responsable?</h3><p class="field-help">Selecciona el apartado y describe la corrección solicitada. Puedes agregar varios incisos.</p><div id="volunteer-inciso-list"></div><button type="button" class="btn secondary small" id="add-volunteer-inciso">+ Añadir inciso observado</button></div>`:'';
 const body=`<form id="rsu-review-form">${reviewSummaryHTML(type,detail,phase)}<div class="hint">Revisa el expediente y aplica la ficha correspondiente al Reglamento de RSU. Si existen observaciones, el servidor registra un plazo de <b>3 días hábiles</b> para subsanarlas. La aprobación requiere las revisiones institucionales asignadas.</div>${annex5InReview}${criteria.length?`<h3 class="review-subheading">Criterios de evaluación</h3>${criteriaHTML}`:`<div class="criterion-evidence volunteer-evidence"><b>Plan e información presentada</b><div class="evidence-text">${esc(Object.entries(reviewContent(type,detail,phase)).filter(([k])=>!/(id|base64|url|firma)/i.test(k)).map(([k,v])=>`${prettify(k)}: ${typeof v==='object'?JSON.stringify(v):v}`).join('\n').slice(0,3500))}</div></div><div class="form-field"><label class="field-label">Resultado de revisión</label><select id="rsu-review-result" class="input"><option value="aprobado">Aprobar</option><option value="observado">Enviar correcciones</option></select></div>${volunteerFieldHTML}`}<div class="form-field full" style="margin-top:16px"><label class="field-label">Observaciones generales</label><textarea id="rsu-review-notes" class="input" rows="4" placeholder="Describe de forma precisa lo que el responsable debe subsanar. Si todo cumple, deja este campo vacío."></textarea></div><p class="field-help">Toda observación queda registrada en el expediente y notificada al responsable.</p></form>`;
 openDialog(title,body,btn('Volver al expediente','dialog-back','ghost')+'<button form="rsu-review-form" type="submit" class="btn" id="rsu-review-submit">Registrar evaluación</button>',()=>openEntity(type,id));
 const reviewEnableAnnex5=$('[data-cmd=enable-annex5-review]',$('#dialog-host'));
 if(reviewEnableAnnex5&&!reviewEnableAnnex5.disabled)reviewEnableAnnex5.onclick=()=>enableProjectAnnex5(id,detail,reviewEnableAnnex5);
 $$('.workflow-criterion',$('#dialog-host')).forEach(r=>{$('.criterion-choice',r).onchange=()=>{$('.criterion-note',r).hidden=$('.criterion-choice',r).value==='true'}});
 if(type==='volunteers'){
  const selector=$('#rsu-review-result'),panel=$('#volunteer-observed-fields'),list=$('#volunteer-inciso-list');
  const add=()=>{const row=mk('div','rsu-volunteer-inciso-row');row.dataset.volunteerIssue='1';
   const select=mk('select','input');select.innerHTML=volunteerIncisos.map(([k,v])=>`<option value="${esc(k)}">${esc(v)}</option>`).join('');
   const text=mk('textarea','input');text.rows=2;text.placeholder='Indica específicamente qué debe corregirse en este inciso';
   const remove=mk('button','btn ghost small','Eliminar');remove.type='button';remove.onclick=()=>row.remove();row.append(select,text,remove);list.append(row)};
  const sync=()=>{panel.hidden=selector.value!=='observado';if(!panel.hidden&&!list.children.length)add()};
  selector.onchange=sync;$('#add-volunteer-inciso').onclick=add;sync();
 }
 $('#rsu-review-form').onsubmit=async(e)=>{e.preventDefault();const button=$('#rsu-review-submit');const all=$$('.workflow-criterion',$('#dialog-host'));const observations=$('#rsu-review-notes').value.trim();let payload;
 if(type==='projects'){
  const result={},notes={};for(const el of all){const key=el.dataset.criterion,cumple=$('.criterion-choice',el).value==='true';result[key]=cumple;const note=$('.criterion-note',el).value.trim();if(!cumple&&!note){toast('Escribe la observación específica de cada criterio marcado NO.',true);return}if(note)notes[key]=note;}
  if(Object.values(result).every(Boolean)&&observations){toast('Para enviar observaciones, marca NO en al menos un criterio.',true);return}
  payload={criterios:result,observaciones_por_criterio:notes,observaciones:observations||null,firmas:[]};
 }else if(type==='activities'){
  const criteria=all.map(el=>({clave:el.dataset.criterion,cumple:$('.criterion-choice',el).value==='true',observacion:$('.criterion-note',el).value.trim()||null}));
  if(criteria.some(c=>!c.cumple&&!c.observacion)&&!observations){toast('Explica los criterios incumplidos o registra una observación general.',true);return}
  if(criteria.every(c=>c.cumple)&&observations){toast('Para solicitar correcciones, marca al menos un criterio como NO.',true);return}
  payload={criterios:criteria,observaciones:observations||null};
 }else{
  const resultado=$('#rsu-review-result').value;
  const incisos=resultado==='observado'?$$('[data-volunteer-issue]',$('#dialog-host')).map(row=>({key:$('select',row).value,note:$('textarea',row).value.trim()})):[];
  if(incisos.some(x=>!x.note)){toast('Describe la corrección de cada inciso seleccionado.',true);return;}
  const fullNotes=[...incisos.map(x=>`[INCISO:${x.key}] ${x.note}`),observations].filter(Boolean).join('\n');
  if(resultado==='observado'&&!fullNotes){toast('Describe qué debe corregir el responsable.',true);return;}
  payload={resultado,observaciones:fullNotes||null};
 }
 if(!confirm('¿Registrar la evaluación institucional? Esta decisión se añadirá al historial del expediente.'))return;
 button.disabled=true;button.textContent='Registrando evaluación…';try{await api(path,{method:'POST',body:payload});toast('Evaluación registrada. El expediente y las notificaciones fueron actualizados.');await openEntity(type,id);refreshUnread().catch(()=>{});}catch(err){toast(err.message,true);button.disabled=false;button.textContent='Registrar evaluación';}
 };
}
async function renderEvaluationInbox(){
 if(!isReviewer()){
  setPage(pageHeading('Mis evaluaciones','Seguimiento de las propuestas y sus observaciones')+moduleBanner('evaluations','Historial de revisiones','Abre un expediente para consultar observaciones, plazos y documentos habilitados.')+`<div class="hint">Solo la Dirección de RSU y los revisores institucionalmente asignados pueden aprobar u observar propuestas.</div><div class="workflow-actions">${btn('Ver proyectos','go-projects','secondary')}${btn('Ver actividades','go-activities','secondary')}${btn('Ver voluntariados','go-volunteers','secondary')}</div>`);
  for(const [name,type] of [['projects','projects'],['activities','activities'],['volunteers','volunteers']])$(`[data-cmd="go-${name}"]`).onclick=()=>navigate(type);return;
 }
 const all=await Promise.allSettled(Object.entries(MODULE_ENDPOINTS).map(async ([type,path])=>(await api(path)).map(r=>({...r,_type:type}))));
 const rows=all.flatMap(r=>r.status==='fulfilled'?r.value:[]);const pendingStages=['evaluacion_inicial','evaluacion_final'];
 const pending=rows.filter(r=>pendingStages.includes(String(r.etapa_actual||'')));
 const observed=rows.filter(r=>/observ|subsan/i.test(String(r.estado_flujo||'')));
 const filtered=rows.filter(r=>pending.includes(r)||observed.includes(r));
 setPage(pageHeading('Bandeja de evaluaciones','Propuestas recibidas, correcciones y aprobaciones por modalidad',btn('Actualizar bandeja','refresh-evals','secondary'))+
 moduleBanner('evaluations','Evaluación institucional','La evaluación se registra mediante los criterios de los anexos oficiales. Si se observa una propuesta, el responsable dispone de 3 días hábiles para corregirla.')+
 `<div class="metric-grid">${renderStats({'Propuestas registradas':rows.length,'Por revisar':pending.length,'Con observaciones':observed.length,'En otras etapas':rows.length-pending.length-observed.length})}</div>`+
 `<div class="card card-pad"><div class="section-title"><h3>Propuestas en evaluación o subsanación</h3><span class="small-k">${filtered.length} expedientes</span></div><div class="toolbar"><input id="evaluation-search" class="search" placeholder="Buscar responsable, título o modalidad…"><select id="evaluation-filter" class="input desktop-filter"><option value="">Todas las modalidades</option><option value="projects">Proyectos RSU</option><option value="activities">Actividades específicas</option><option value="volunteers">Voluntariado</option></select></div><div id="evaluation-queue">${table(filtered,{columns:['titulo','responsable','periodo_academico','estado_flujo','etapa_actual','fecha_limite_subsanacion'],controls:(r,i)=>btn('Abrir y evaluar',`eval-open-${i}`,'secondary small')})}</div></div>`);
 filtered.forEach((r,i)=>{$(`[data-cmd="eval-open-${i}"]`).onclick=()=>openEntity(r._type,r.proyecto_id)});
 $('[data-cmd=refresh-evals]').onclick=()=>renderEvaluationInbox();
 const filter=()=>{const q=$('#evaluation-search').value.toLowerCase(),m=$('#evaluation-filter').value;$$('#evaluation-queue tbody tr').forEach((tr,i)=>{tr.hidden=Boolean((q&&!tr.innerText.toLowerCase().includes(q))||(m&&filtered[i]._type!==m))})};
 $('#evaluation-search').oninput=filter;$('#evaluation-filter').onchange=filter;
}
function renderDetails(obj,depth=0,parentKey=''){
 if(obj==null)return '<p class="muted">Sin información.</p>';
 if(Array.isArray(obj)){
  if(!obj.length)return '<p class="muted">No hay elementos registrados.</p>';
  if(obj.every(r=>r&&typeof r==='object'&&!Array.isArray(r)))return table(obj,{columns:displayColumns(obj[0],9)});
  return `<ul class="readable-list">${obj.map(v=>`<li>${esc(typeof v==='object'?'Información registrada':showField(parentKey,v))}</li>`).join('')}</ul>`;
 }
 if(typeof obj!=='object')return `<span>${esc(showField(parentKey,obj))}</span>`;
 const valid=Object.entries(obj).filter(([k])=>displayKeyInRecord(k,obj));
 const flat=valid.filter(([,v])=>v===null||typeof v!=='object');
 const nested=valid.filter(([,v])=>v!==null&&typeof v==='object');
 return `<div class="detail-grid">${flat.map(([k,v])=>`<div class="kv"><div class="key">${esc(colTitle(k))}</div><div class="val">${/estado|estatus/i.test(k)?statusPill(showField(k,v,obj)):esc(showField(k,v,obj))}</div></div>`).join('')}</div>${nested.map(([k,v])=>`<div class="detail-section"><h3>${esc(colTitle(k))}</h3>${depth<3?renderDetails(v,depth+1,k):'<p class="muted">Información disponible en el expediente.</p>'}</div>`).join('')}`
}
function showDetail(title,data){openDialog(title,renderDetails(data),btn('Cerrar','close','ghost'))}
async function simpleAction(method,path,question){if(question&&!confirm(question))return;try{const res=await api(path,{method,body:method==='GET'?null:{}});toast(res?.message||'Operación completada');closeDialog();if(path.includes('convocatorias'))await loadLookup();await navigate(state.active)}catch(e){toast(e.message,true)}}
async function renderPeople(){const list=await api('/personas');state.rows=list;setPage(pageHeading('Gestión de personal','Administración de estudiantes, docentes, egresados y personal de RSU',btn('+ Registrar persona','new-person','primary'))+moduleBanner('people','Registro de personal universitario','Consulta, registro y actualización de personas vinculadas a la Dirección de RSU.')+
 `<div class="toolbar"><input class="search" id="people-search" placeholder="Buscar personas por nombre, documento o código…"><select class="input" style="width:180px" id="people-kind"><option value="">Todos los perfiles</option>${['administrativo','docente','estudiante','no_docente','egresado','graduado'].map(x=>`<option>${x}</option>`).join('')}</select><span class="small-k">${list.length} personas</span></div><div class="card" id="people-table">${table(list,{columns:['nombre_completo','dni','tipo_persona','email_institucional','codigo_estudiante','activo'],controls:(r,i)=>btn('Ver',`person-detail-${i}`,'ghost small')+btn('Editar',`person-edit-${i}`,'secondary small')})}</div>`);
 const refresh=async()=>{const q=$('#people-search').value,tipo=$('#people-kind').value;const qp=new URLSearchParams();if(q)qp.set('q',q);if(tipo)qp.set('tipo',tipo);const result=await api(`/personas?${qp}`);state.rows=result;$('#people-table').innerHTML=table(result,{columns:['nombre_completo','dni','tipo_persona','email_institucional','codigo_estudiante','activo'],controls:(r,i)=>btn('Ver',`person-detail-${i}`,'ghost small')+btn('Editar',`person-edit-${i}`,'secondary small')});wirePeople(result)};
 const wirePeople=rows=>rows.forEach((r,i)=>{const id=r.persona_id;$(`[data-cmd="person-detail-${i}"]`).onclick=async()=>showDetail('Datos de la persona',await api(`/personas/${encodeURIComponent(id)}`));$(`[data-cmd="person-edit-${i}"]`).onclick=async()=>openAction('PUT','/personas/{persona_id}',{persona_id:id},await api(`/personas/${encodeURIComponent(id)}`))});
 wirePeople(list);$('#people-search').onchange=refresh;$('#people-kind').onchange=refresh;
 $('[data-cmd=new-person]').onclick=()=>openChoosePerson();
}
function openChoosePerson(){openDialog('Registrar nueva persona',`<p class="sub">Elige el perfil institucional para completar sus datos. Los campos obligatorios se validarán en FastAPI.</p><div class="quick-links" style="margin-top:24px">${[['administrativo','Personal administrativo'],['docente','Docente'],['estudiante','Estudiante'],['vinculado','No docente, egresado o graduado']].map(([k,n])=>`<button class="quick-link" data-register="${k}">♙ ${esc(n)}</button>`).join('')}</div>`);$$('[data-register]',$('#dialog-host')).forEach(x=>x.onclick=()=>openAction('POST',`/personas/registrar/${x.dataset.register}`))}
async function renderRoles(){const d=await api('/roles-autoridades/resumen');const actions=btn('+ Crear rol','role-new','primary')+btn('+ Asignar autoridad','role-assign','secondary');setPage(pageHeading('Roles y autoridades','Asignación de responsables y control de perfiles institucionales',actions)+moduleBanner('roles','Roles y autoridades','Administra las asignaciones de Dirección, facultades y responsables con los permisos autorizados.')+`<div class="metric-grid">${renderStats(d.estadisticas||{})}</div><div class="card card-pad"><div class="section-title"><h3>Asignaciones vigentes y registradas</h3></div>${table(d.asignaciones||[],{columns:['persona_nombre','rol_nombre','facultad','cargo_oficial','activo'],controls:(r,i)=>btn('Editar',`role-edit-${i}`,'secondary small')})}</div><div class="card card-pad" style="margin-top:16px"><div class="section-title"><h3>Roles disponibles</h3></div>${table(d.roles||[],{columns:['codigo','nombre','descripcion','activo']})}</div>`);
 $('[data-cmd=role-new]').onclick=()=>openAction('POST','/roles-autoridades/roles');$('[data-cmd=role-assign]').onclick=()=>openAction('POST','/roles-autoridades/asignaciones');
 (d.asignaciones||[]).forEach((r,i)=>{$(`[data-cmd="role-edit-${i}"]`).onclick=()=>openAction('PUT','/roles-autoridades/asignaciones/{assignment_id}',{assignment_id:r.user_system_role_id},r)});
}
async function renderNotifications(tab='todas'){const r=await api('/notificaciones?'+new URLSearchParams({tab,limit:100}));const items=r.items||[];const tabs=[['todas','Todas'],['no_leidas','No leídas'],['importantes','Importantes'],['archivadas','Archivadas']];setPage(pageHeading('Centro de notificaciones','Observaciones, convocatorias, vencimientos y comunicaciones institucionales',btn('Marcar todas como leídas','read-all','ghost'))+moduleBanner('notifications','Bandeja de notificaciones','Revisa observaciones de propuestas, plazos de entrega y avisos del área de RSU.')+`<div class="nav-tabs">${tabs.map(([k,n])=>`<button class="tab ${k===tab?'active':''}" data-tab="${k}">${n}</button>`).join('')}</div><div class="card card-pad"><div class="section-title"><h3>Mensajes (${esc(r.total_filtrado||0)})</h3></div>${items.map((n,i)=>`<div class="activity-entry"><span class="activity-dot">${n.leida?'✓':'✉'}</span><div style="flex:1"><b>${esc(n.titulo||'Notificación')}</b> ${n.importante?'⭐':''}<p>${esc(n.mensaje||'')}</p><div class="record-meta"><span class="small-k">${esc(n.fecha_creacion||'')}</span>${statusPill(n.tipo||n.modulo||'Aviso')}</div><div class="btnset" style="margin-top:9px">${btn('Leída',`notif-read-${i}`,'ghost small')}${btn('Importante',`notif-star-${i}`,'ghost small')}${btn('Archivar',`notif-archive-${i}`,'ghost small')}</div></div></div>`).join('')||empty('No hay notificaciones en esta bandeja.')}</div>`);
 $$('[data-tab]').forEach(x=>x.onclick=()=>renderNotifications(x.dataset.tab));
 $('[data-cmd=read-all]').onclick=async()=>{await api('/notificaciones/acciones/leer-todas',{method:'PATCH',body:{}});toast('Bandeja actualizada');await renderNotifications(tab);refreshUnread()};
 items.forEach((n,i)=>[['read','leer',{valor:!n.leida}],['star','importante',{valor:!n.importante}],['archive','archivar',{valor:!n.archivada}]].forEach(([k,s,b])=>{$(`[data-cmd="notif-${k}-${i}"]`).onclick=async()=>{try{await api(`/notificaciones/${n.notificacion_id||n.notification_id}/${s}`,{method:'PATCH',body:b});await renderNotifications(tab);refreshUnread()}catch(e){toast(e.message,true)}}}));
}
async function renderReport(kind){const type=kind==='reportes'?'Reportes institucionales':'Evaluaciones';const data=await api(`/flujo-rsu/${kind}/dashboard`);const res=data;const stats=data.counts||data.summary||data.metrics||data.estadisticas||{};setPage(pageHeading(type,kind==='reportes'?'Indicadores por periodo y facultad; exportación de información oficial':'Revisión de proyectos, actividades y voluntariados',kind==='reportes'?btn('Exportar Excel','export-reports','primary'):btn('Ver proyectos','goto-projects','secondary'))+moduleBanner(kind==='reportes'?'reports':'evaluations',type,kind==='reportes'?'Consulta la información consolidada y exporta reportes institucionales.':'Consulta el estado de los expedientes y sus procesos de revisión.')+`<div class="metric-grid">${renderStats(stats)}</div><div class="card card-pad">${renderDetails(Object.fromEntries(Object.entries(res).filter(([k])=>!['counts','summary','metrics','estadisticas'].includes(k))))}</div>`);
 if(kind==='reportes')$('[data-cmd=export-reports]').onclick=()=>doDownload('/flujo-rsu/reportes/exportar-excel');else $('[data-cmd=goto-projects]').onclick=()=>navigate('projects')
}
async function renderHours(){const d=await api('/flujo-rsu/modulos/inicio');const h=d.counts?.horas_rsu_realizadas??0;setPage(pageHeading('Horas de RSU','Consolidado institucional de actividades y participación')+moduleBanner('hours','Control de horas RSU','Distingue la participación registrada de las horas certificadas después de su validación institucional.')+`<div class="metric-grid"><div class="card metric"><div class="metric-ico">◷</div><div class="value">${Number(h).toLocaleString('es-PE')}</div><div class="metric-label">Horas-persona reportadas</div></div></div><div class="hint">El indicador proviene de los servicios de la API y puede integrar jornadas, informes y certificados. No debe confundirse automáticamente con horas individuales oficialmente acreditadas. Para emitir certificados debe seguirse el procedimiento de cada modalidad.</div><div class="card card-pad"><h3>Consulta y certificación</h3><p class="sub">Para consultar el detalle de tus participaciones, ingresa a los expedientes donde figuras como participante. Los certificados oficiales se generan después de la evaluación y autorización correspondientes.</p><div class="btnset" style="margin-top:18px">${btn('Proyectos RSU','goto-projects','secondary')}${btn('Actividades específicas','goto-activities','secondary')}${btn('Voluntariado','goto-volunteers','secondary')}</div></div>`);['projects','activities','volunteers'].forEach(id=>{$(`[data-cmd="goto-${id}"]`).onclick=()=>navigate(id)})}
const CATALOGS=[
 ['lineas-accion','Líneas de acción','linea_accion_id',true],['tipos-proyecto','Tipos de proyecto','tipo_proyecto_id',true],['roles-proyecto','Roles de participación','rol_id',true],['tipos-documento','Tipos de documento','tipo_documento_id',true],['departamentos-academicos','Departamentos académicos','departamento_academico_id',true],['limites-rol-tipo','Límites por modalidad','limite_rol_id',true],['periodos-academicos','Periodos académicos','periodo_academico_id',false]
];
async function renderCatalogs(tab='lineas-accion'){const current=CATALOGS.find(c=>c[0]===tab)||CATALOGS[0];const rows=await api(`/catalogos/${tab}`);setPage(pageHeading('Configuración institucional','Catálogos que utiliza el servidor para validar propuestas y participantes',btn('+ Nuevo registro','catalog-new','primary'))+moduleBanner('catalogs','Catálogos institucionales','Configura líneas de acción, modalidades y estructura académica, respetando las condiciones del reglamento.')+
 `<div class="nav-tabs">${CATALOGS.map(c=>`<button class="tab ${c[0]===tab?'active':''}" data-catalog="${c[0]}">${esc(c[1])}</button>`).join('')}</div><div class="hint">Las líneas de acción y los requisitos de participación deben ajustarse a los artículos 21, 23, 27 y 29 del reglamento. No modifiques las reglas sin autorización institucional.</div><div class="card card-pad"><div class="section-title"><h3>${esc(current[1])}</h3><span class="small-k">${rows.length} registros</span></div>${table(rows,{columns:Object.keys(rows[0]||{}).filter(k=>k!=='descripcion'&&!k.endsWith('_id')).slice(0,6),controls:(r,i)=>current[3]?btn('Editar',`cat-edit-${i}`,'secondary small')+btn('Desactivar',`cat-del-${i}`,'warn small'):''})}</div>`);
 $$('[data-catalog]').forEach(x=>x.onclick=()=>renderCatalogs(x.dataset.catalog));
 $('[data-cmd=catalog-new]').onclick=()=>openAction('POST',`/catalogos/${tab}`);
 rows.forEach((r,i)=>{if(!current[3])return;const id=r[current[2]]||r.id;
 $(`[data-cmd="cat-edit-${i}"]`).onclick=()=>openAction('PUT',`/catalogos/${tab}/{item_id}`,{item_id:id},r);
 $(`[data-cmd="cat-del-${i}"]`).onclick=()=>simpleAction('DELETE',`/catalogos/${tab}/${encodeURIComponent(id)}`,`¿Desactivar/eliminar este registro de ${current[1]}?`)
 });
}
async function renderTemplates(){const d=await api('/plantillas-anexos');const rows=d.items||[];setPage(pageHeading('Plantillas y anexos','Repositorio de modelos oficiales para proyectos, actividades específicas y voluntariado',d.can_manage?btn('+ Subir plantilla','template-new','primary'):'')+moduleBanner('templates','Formatos oficiales','Gestiona las versiones autorizadas de los anexos del Reglamento de RSU.','Anexos 1–14')+`<div class="hint">Formatos de referencia del Reglamento de RSU: anexos 1 al 14. El servidor conserva las versiones cargadas y autoriza su descarga según el perfil.</div><div class="card card-pad"><div class="section-title"><h3>Documentos disponibles</h3><span class="small-k">${rows.length} plantillas</span></div>${table(rows,{columns:['codigo_anexo','nombre','modulo_nombre','version','fecha_actualizacion'],controls:(r,i)=>btn('PDF',`template-download-${i}`,'secondary small')+(d.can_manage?btn('Editar',`template-edit-${i}`,'ghost small')+btn('Eliminar',`template-delete-${i}`,'warn small'):'' )})}</div>`);
 if(d.can_manage)$('[data-cmd=template-new]').onclick=()=>openAction('POST','/plantillas-anexos');
 rows.forEach((r,i)=>{const id=r.template_id;$(`[data-cmd="template-download-${i}"]`).onclick=()=>doDownload(`/plantillas-anexos/${encodeURIComponent(id)}/descargar`);if(!d.can_manage)return;
 $(`[data-cmd="template-edit-${i}"]`).onclick=()=>openAction('PUT','/plantillas-anexos/{template_id}',{template_id:id},r);
 $(`[data-cmd="template-delete-${i}"]`).onclick=()=>simpleAction('DELETE',`/plantillas-anexos/${encodeURIComponent(id)}`,'¿Eliminar esta versión de plantilla?')});
}
async function renderAudit(){const d=await api('/auditoria?limit=150');setPage(pageHeading('Auditoría del sistema','Historial de operaciones realizadas en la plataforma')+moduleBanner('audit','Historial de operaciones','Revisa eventos y cambios de la plataforma según tu perfil autorizado.')+`<div class="metric-grid">${renderStats(d.summary||{})}</div><div class="card card-pad"><div class="section-title"><h3>Operaciones recientes</h3><span class="small-k">${d.total||0} resultados</span></div>${table(d.items||[],{columns:['fecha_hora','username','accion','modulo','entidad','detalle','estado_http']})}</div>`)}
async function renderTestLab(){
 const status=await api('/herramientas-prueba/estado');
 const found=await Promise.allSettled(Object.entries(MODULE_ENDPOINTS).map(async ([mod,path])=>(await api(path)).filter(x=>String(x.titulo||'').toUpperCase().startsWith('[PRUEBA]')).map(x=>({...x,_module:mod}))));
 const records=found.flatMap(r=>r.status==='fulfilled'?r.value:[]);
 const hint=status.habilitado?'<div class="stage-confirmation"><b>Simulaciones habilitadas en este servidor local.</b><p>Solo expedientes cuyo título comienza con [PRUEBA]. Los estados de ensayo no equivalen a aprobaciones institucionales.</p></div>':'<div class="notice">Las simulaciones están desactivadas por seguridad. Solo en un entorno local aislado puedes establecer APP_ENV=local y RSU_ENABLE_TEST_ADVANCE=true en el .env del servidor, y reiniciar FastAPI.</div>';
 setPage(pageHeading('Pruebas de etapas','Adelanta documentos solo en expedientes de ensayo, sin generar aprobaciones ni certificados')+
  moduleBanner('operations','Simulación controlada de procesos','Área exclusiva del administrador RSU, separada de los expedientes oficiales.','SOLO PRUEBAS')+hint+
  `<div class="card card-pad" style="margin-top:15px"><div class="section-title"><h3>Expedientes [PRUEBA]</h3><span class="small-k">${records.length} registros</span></div><div class="toolbar"><input id="search-tests" class="search" placeholder="Buscar proyecto o responsable…"></div><div id="test-table">${table(records,{columns:['titulo','responsable','estado_flujo','etapa_actual'],controls:(r,i)=>btn('Abrir expediente',`test-open-${i}`,'secondary small')+(status.habilitado?btn('Habilitar siguiente',`test-advance-${i}`,'warn small'):'')})}</div></div>`);
 const bind=()=>{records.forEach((row,i)=>{$(`[data-cmd=test-open-${i}]`)?.addEventListener('click',()=>openEntity(row._module,row.proyecto_id));const b=$(`[data-cmd=test-advance-${i}]`);if(b)b.onclick=async()=>{const reason=prompt('Motivo de la simulación (al menos 10 caracteres):','Ensayo de habilitación de anexos RSU');if(!reason)return;if(!confirm('Solo para pruebas: se cambiará la etapa del expediente sin registrar una aprobación oficial. ¿Continuar?'))return;b.disabled=true;try{const res=await api(`/herramientas-prueba/expedientes/${row._module}/${encodeURIComponent(row.proyecto_id)}/habilitar-siguiente`,{method:'POST',body:{motivo:reason}});toast(res.detalle||'Simulación registrada');await renderTestLab()}catch(err){toast(err.message,true);b.disabled=false}}})};bind();
 $('#search-tests').oninput=()=>{const q=$('#search-tests').value.trim().toLowerCase();const visible=records.filter(r=>`${r.titulo} ${r.responsable} ${r.estado_flujo}`.toLowerCase().includes(q));$('#test-table').innerHTML=table(visible,{columns:['titulo','responsable','estado_flujo','etapa_actual'],controls:(r)=>{const i=records.indexOf(r);return btn('Abrir expediente',`test-open-${i}`,'secondary small')+(status.habilitado?btn('Habilitar siguiente',`test-advance-${i}`,'warn small'):'')}});bind()};
}
async function renderOperations(){const valid=Object.entries(state.spec?.paths||{}).filter(([path])=>path.startsWith(API+'/')).flatMap(([full,methods])=>{const path=full.slice(API.length);return Object.entries(methods).filter(([method])=>['post','put','patch','delete'].includes(method)).map(([method,def])=>({method:method.toUpperCase(),path,title:def.summary||prettify(def.operationId)}))});
 const groups={};valid.forEach(x=>{const group=x.path.split('/').filter(Boolean)[0];(groups[group]??=[]).push(x)});
 setPage(pageHeading('Herramientas avanzadas','Operaciones adicionales de la API, sujetas a autorización del servidor')+`<div class="notice">Las operaciones de esta sección afectan datos institucionales. Úsalas únicamente con el rol y autorización adecuados. Las acciones de proyectos, actividades y voluntariado también están disponibles desde sus expedientes.</div><div class="toolbar"><input class="search" id="ops-search" placeholder="Buscar operación por nombre o ruta…"><span class="small-k">${valid.length} operaciones disponibles en FastAPI</span></div>${Object.entries(groups).map(([g,ops])=>`<div class="card card-pad" style="margin-bottom:16px" data-ops-group><h3 style="margin-bottom:13px">${esc(prettify(g))}</h3><div class="api-ops">${ops.map((op,i)=>`<button class="btn ghost small" style="justify-content:start;text-align:left" data-api-op="${esc(op.method+' '+op.path)}">${esc(op.method)} · ${esc(op.title)}</button>`).join('')}</div></div>`).join('')}`);
 searchBind('#ops-search','[data-ops-group] .btn');$$('[data-api-op]').forEach(x=>x.onclick=()=>{const s=x.dataset.apiOp,p=s.slice(s.indexOf(' ')+1),m=s.split(' ')[0];openAction(m,p)})
}
let dialogBackAction=null;
function openDialog(title,html,footer='',onBack=null){document.body.style.overflow='hidden';dialogBackAction=onBack;
 document.removeEventListener('keydown',onModalEscape);
 $('#dialog-host').innerHTML=`<div class="modal-backdrop" id="backdrop"><section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><header class="modal-head"><div class="modal-nav-line"><button type="button" class="btn ghost small" data-cmd="dialog-back" aria-label="Volver al apartado anterior">← Atrás</button><div><div class="eyebrow">Sistema RSU · UNAB</div><h2 id="dialog-title">${esc(title)}</h2></div></div><button type="button" class="icon-btn" aria-label="Cerrar" data-cmd="close">×</button></header><div class="modal-body">${html}</div><div class="modal-foot">${footer||btn('Cerrar','close','ghost')}</div></section></div>`;
 $$('#dialog-host [data-cmd="close"]').forEach(b=>b.onclick=closeDialog);
 $$('[data-cmd="dialog-back"]',$('#dialog-host')).forEach(button=>button.onclick=()=>{const goBack=dialogBackAction;dialogBackAction=null;if(typeof goBack==='function')goBack();else closeDialog()});
 const backdrop=$('#backdrop');backdrop.onclick=e=>{if(e.target===backdrop)closeDialog()};document.addEventListener('keydown',onModalEscape)}
function onModalEscape(e){if(e.key==='Escape')closeDialog();else if(e.altKey&&e.key==='ArrowLeft'){e.preventDefault();$('[data-cmd="dialog-back"]',$('#dialog-host'))?.click()}}
function closeDialog(){dialogBackAction=null;document.body.style.overflow='';document.removeEventListener('keydown',onModalEscape);$('#dialog-host').innerHTML=''}
function expandPath(path,params={}){return path.replace(/\{([^}]+)\}/g,(m,key)=>{if(params[key]===undefined||params[key]==='')throw new Error(`Falta indicar el parámetro ${key}.`);return encodeURIComponent(params[key])})}
const labels={titulo:'Título',linea_accion:'Línea de acción',periodo_academico:'Periodo académico',responsable:'Responsable',linea_accion_id:'Línea de acción',periodo_academico_id:'Periodo académico',tipo_proyecto_id:'Modalidad de RSU',convocatoria_id:'Convocatoria',lugar_ejecucion:'Lugar de ejecución',lugar_entorno:'Lugar o entorno de ejecución',poblacion_objetivo:'Población beneficiaria',resumen:'Resumen',problematica:'Problemática por abordar',objetivo:'Objetivo',objetivos:'Objetivos',metodologia:'Metodología y estrategias',impacto_social:'Impacto social',impacto_estudiantes:'Impacto en los estudiantes',fecha_inicio:'Fecha de inicio',fecha_fin:'Fecha de término',fecha_limite_presentacion:'Fecha límite de presentación',miembros:'Integrantes del equipo',colaboradores:'Colaboradores',cronograma:'Cronograma de ejecución',financiado:'Proyecto financiado',presupuesto:'Presupuesto del proyecto',firmas:'Firmas',hora:'Hora',horas_totales:'Horas totales',horas_certificadas:'Horas que se acreditarán',observaciones:'Observaciones',observaciones_por_criterio:'Observaciones por criterio',email_institucional:'Correo institucional',dni:'DNI',persona_id:'Persona',docente_asesor:'Docente asesor',departamento_academico_id:'Departamento académico',system_role_id:'Rol institucional',firma_responsable:'Firma del responsable',foto:'Fotografía',huella:'Huella digital',proyecto_id:'Proyecto',project_id:'Proyecto',user_system_role_id:'Asignación institucional',catalogo_estado_id:'Estado',responsable_persona_id:'Responsable',evaluador_persona_id:'Evaluador',revisor_persona_id:'Revisor',miembro_afectado_id:'Integrante',reemplazo_propuesto_id:'Reemplazo propuesto',tipo_documento_id:'Tipo de documento',rol_id:'Rol de participación',tipo_usuario:'Tipo de usuario',estado_flujo:'Estado del procedimiento',etapa_actual:'Etapa',tipo_proyecto:'Modalidad'};
const labelOf=name=>labels[name]||prettify(name);
const refs={linea_accion_id:['lineas','linea_accion_id',['codigo','nombre']],tipo_proyecto_id:['tipos','tipo_proyecto_id',['nombre']],periodo_academico_id:['periodos','periodo_academico_id',['nombre']],departamento_academico_id:['departamentos','departamento_academico_id',['facultad','carrera','nombre']],convocatoria_id:['convocatorias','convocatoria_id',['tipo_proyecto','periodo_academico']],rol_id:['rolesproyecto','rol_id',['nombre']],system_role_id:['roles','system_role_id',['nombre']],persona_id:['personas','persona_id',['nombre_completo','dni']],miembro_afectado_id:['personas','persona_id',['nombre_completo','dni']],reemplazo_propuesto_id:['personas','persona_id',['nombre_completo','dni']],tipo_documento_id:['tiposdoc','tipo_documento_id',['nombre']],estado_id:['estados','catalogo_estado_id',['entidad','nombre']],catalogo_estado_id:['estados','catalogo_estado_id',['entidad','nombre']],proyecto_id:['expedientes','proyecto_id',['titulo','responsable']],project_id:['expedientes','proyecto_id',['titulo','responsable']]};
async function loadLookup(){
 const tasks=[['lineas','/catalogos/lineas-accion'],['tipos','/catalogos/tipos-proyecto'],['periodos','/catalogos/periodos-academicos'],['departamentos','/catalogos/departamentos-academicos'],['convocatorias','/flujo-rsu/convocatorias'],['convocatoriasActivas','/flujo-rsu/convocatorias/activas'],['rolesproyecto','/catalogos/roles-proyecto'],['personas','/flujo-rsu/personas-seleccion'],['roles','/roles-autoridades/resumen'],['tiposdoc','/catalogos/tipos-documento'],['estados','/catalogos/estados']];
 await Promise.allSettled(tasks.map(async([key,path])=>{const result=await api(path);state.lookup[key]=key==='roles'?(result.roles||[]):(Array.isArray(result)?result:(result.items||[]))}));
 if(state.user?.persona_id&&!state.lookup.personas?.some(x=>String(x.persona_id)===String(state.user.persona_id))){state.lookup.personas=[...(state.lookup.personas||[]),{persona_id:state.user.persona_id,nombre_completo:state.user.nombre_completo||'Mi cuenta',dni:state.user.dni||''}]};
 const collections=await Promise.allSettled(Object.values(MODULE_ENDPOINTS).map(path=>api(path)));
 state.lookup.expedientes=collections.flatMap(res=>res.status==='fulfilled'&&Array.isArray(res.value)?res.value:[]);
}

function resolveSchema(s){if(!s)return {};if(s.$ref){let name=s.$ref.split('/').pop();return resolveSchema(state.spec?.components?.schemas?.[name]||{})}if(s.allOf){return {...s,...resolveSchema(s.allOf[0]),allOf:undefined}}if(s.anyOf){const filtered=s.anyOf.filter(x=>x.type!=='null');return {...s,...resolveSchema(filtered[0]),anyOf:undefined,nullable:s.anyOf.some(x=>x.type==='null')}}return s}
function getOperation(method,path){let op=state.spec?.paths?.[API+path]?.[method.toLowerCase()]||state.spec?.paths?.[path]?.[method.toLowerCase()];return op||null}
function guessTitle(method,path){const op=getOperation(method,path);return op?.summary||`${method} ${prettify(path.split('/').filter(Boolean).slice(-1)[0])}`}
function getBodySpec(op){const ct=op?.requestBody?.content||{};const contentType=Object.keys(ct).find(x=>x.includes('multipart/form-data'))||Object.keys(ct).find(x=>x.includes('application/json'))||Object.keys(ct)[0];return {contentType,schema:resolveSchema(ct[contentType]?.schema||{}),hasBody:!!op?.requestBody}}
function schemaDefault(schema){schema=resolveSchema(schema);if(schema.default!==undefined)return schema.default;if(schema.type==='array')return [];if(schema.type==='object'){const o={};Object.entries(schema.properties||{}).forEach(([k,v])=>{if((schema.required||[]).includes(k)){const val=schemaDefault(v);if(val!==null)o[k]=val}});return o}if(schema.type==='boolean')return false;if(schema.type==='integer'||schema.type==='number')return null;if(schema.type==='string')return '';return null}
function mk(tag,cls='',text=''){let el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el}
function fieldsFromSchema(schema,existing={}){const box=mk('div','form-grid');const s=resolveSchema(schema);if(s.properties)Object.entries(s.properties).forEach(([k,sub])=>box.append(createField(k,sub,(s.required||[]).includes(k),existing?.[k])));return box}
function isEncodedFile(s){return !!s.properties&&('data_base64'in s.properties)&&('filename'in s.properties)}
// Selector contextual: búsqueda por nombre/DNI, rol explícito y firma individual.
function memberModule(){const op=state.formPath||'';if(op.includes('/actividades-especificas'))return 'activities';if(op.includes('/voluntariados'))return 'volunteers';return 'projects'}
function memberKindAllowed(module,role,person){const kind=String(person.tipo_persona||'').toLowerCase();
 if(module==='activities')return role==='colaborador'?kind==='estudiante':kind==='docente';
 if(module==='projects'&&role==='corresponsable')return kind==='docente';
 return true;
}
function signaturePad(label,personId=''){const pad=mk('div','member-signature');pad.dataset.signatureLabel=label;pad.dataset.personId=personId;
 const heading=mk('label','field-label',`Firma de ${label}`);const help=mk('p','field-help','La persona debe firmar en el recuadro o adjuntar una imagen de su firma con su autorización.');
 const canvas=mk('canvas','signature-canvas');canvas.width=600;canvas.height=125;canvas.setAttribute('aria-label',`Dibujar firma de ${label}`);
 const file=mk('input','input');file.type='file';file.accept='image/png,image/jpeg,image/webp';file.setAttribute('aria-label',`Adjuntar firma de ${label}`);
 const clear=mk('button','btn ghost small','Borrar firma');clear.type='button';const notice=mk('small','field-help','Pendiente de firma');
 pad.append(heading,help,canvas,file,clear,notice);pad._canvas=canvas;pad._file=file;pad._drawn=false;pad._notice=notice;
 const ctx=canvas.getContext('2d');ctx.lineWidth=2.4;ctx.strokeStyle='#1e3a5f';ctx.lineCap='round';ctx.lineJoin='round';let drawing=false;
 const pos=e=>{const r=canvas.getBoundingClientRect();return {x:(e.clientX-r.left)*canvas.width/r.width,y:(e.clientY-r.top)*canvas.height/r.height}};
 canvas.addEventListener('pointerdown',e=>{drawing=true;canvas.setPointerCapture(e.pointerId);const p=pos(e);ctx.beginPath();ctx.moveTo(p.x,p.y)});
 canvas.addEventListener('pointermove',e=>{if(!drawing)return;const p=pos(e);ctx.lineTo(p.x,p.y);ctx.stroke();pad._drawn=true;notice.textContent='Firma dibujada'});
 canvas.addEventListener('pointerup',()=>drawing=false);canvas.addEventListener('pointercancel',()=>drawing=false);
 clear.onclick=()=>{ctx.clearRect(0,0,canvas.width,canvas.height);pad._drawn=false;file.value='';notice.textContent='Pendiente de firma'};
 file.onchange=()=>{if(file.files[0])notice.textContent=`Firma adjunta: ${file.files[0].name}`};return pad;
}
async function readSignature(pad,personId,label,required=true){if(!pad)return null;const file=pad._file.files?.[0];if(!file&&!pad._drawn){if(required)throw new Error(`Falta la firma de ${label}. Pide que la persona firme en su recuadro.`);return null}
 if(file&&file.size>8*1024*1024)throw new Error(`La firma de ${label} supera 8 MB.`);
 return {filename:file?.name||'firma-digital.png',mime_type:file?.type||'image/png',data_base64:file?await fileAsBase64(file):pad._canvas.toDataURL('image/png').split(',')[1],label:`Firma ${label}`,person_id:String(personId)};
}
function memberEditor(name,schema,required,value){const wrap=mk('div','form-field member-editor full');wrap.dataset.type='member-array';wrap.dataset.key=name;wrap._schema=resolveSchema(schema);
 const content=resolveSchema(resolveSchema(schema).items);const fields=content.properties||{},rolesSchema=resolveSchema(fields.rol||fields.role||{});
 let roleOptions=Array.isArray(rolesSchema.enum)?[...rolesSchema.enum]:['colaborador'];const memberModuleName=memberModule();const leaderType=String(state.user?.tipo_usuario||'').toLowerCase();if(memberModuleName==='projects'&&leaderType==='estudiante')roleOptions=roleOptions.filter(v=>v!=='corresponsable');if(memberModuleName==='activities'){roleOptions=roleOptions.filter(v=>leaderType==='docente'?v!=='docente_asesor':v!=='corresponsable')}const requiresSignature=Boolean(state.formRequiresSignatures);
 wrap.append(mk('label','field-label',labelOf(name)+(required?' *':'')));
 const intro=mk('p','field-help','Busca por nombre o DNI. Los perfiles disponibles se filtran automáticamente según el rol. Cada integrante firma en su propio recuadro.');wrap.append(intro);
 const list=mk('div','member-list');wrap.append(list);
 function appendRow(initial){const card=mk('div','member-card array-row');const top=mk('div','inline-between');top.append(mk('strong','','Integrante '+(list.children.length+1)));
 const remove=mk('button','btn warn small','Quitar');remove.type='button';remove.onclick=()=>card.remove();top.append(remove);card.append(top);
 const roleField=mk('div','form-field');roleField.append(mk('label','field-label','Rol de participación'));
 const roleSelect=mk('select','input member-role');for(const role of roleOptions)roleSelect.append(new Option(prettify(role),role));const initialRole=typeof initial==='object'?(initial?.rol||initial?.role):'colaborador';roleSelect.value=initialRole||roleOptions[0];if(roleOptions.length===1){roleSelect.disabled=true;roleField.append(mk('small','field-help','Rol fijado automáticamente por el reglamento.'))}roleField.append(roleSelect);card.append(roleField);
 const personField=mk('div','form-field');personField.append(mk('label','field-label','Buscar persona / DNI'));const search=mk('input','input member-search');search.placeholder='Buscar por nombre, apellido o DNI';search.autocomplete='off';const select=mk('select','input member-person');select.required=true;personField.append(search,select);card.append(personField);
 const hourField=memberModuleName==='projects'&&fields.horas_semanales?mk('div','form-field member-hours-field'):null;
 if(hourField){hourField.append(mk('label','field-label','Horas semanales del docente'));const hours=mk('input','input member-hours');hours.type='number';hours.min=2;hours.max=5;hours.step=1;hours.value=initial?.horas_semanales??'';hourField.append(hours);card.append(hourField)}
 const existingId=typeof initial==='object'?initial?.persona_id:initial;
 const update=()=>{const role=roleSelect.value,q=search.value.toLowerCase().trim();const old=select.value||String(existingId||'');select.replaceChildren(new Option('Seleccionar persona',''));
 for(const person of state.lookup.personas||[]){if(!memberKindAllowed(memberModuleName,role,person))continue;const label=`${person.nombre_completo||'Persona'} · ${person.dni||'sin DNI'} · ${person.tipo_persona||'Perfil'}`;if(q&&!label.toLowerCase().includes(q))continue;select.append(new Option(label,String(person.persona_id)))}
 if(old&&Array.from(select.options).some(o=>o.value===old))select.value=old;const pad=card.querySelector('.member-signature');if(pad)pad.dataset.personId=select.value;
 if(hourField)hourField.hidden=!((state.lookup.personas||[]).find(x=>String(x.persona_id)===select.value)?.tipo_persona||'').toLowerCase().includes('docente');};
 roleSelect.onchange=update;search.oninput=update;select.onchange=update;
 if(requiresSignature){const signature=signaturePad('integrante');card.append(signature)}list.append(card);update();}
 (Array.isArray(value)?value:[]).forEach(appendRow);
 const add=mk('button','btn secondary small','+ Añadir integrante');add.type='button';add.onclick=()=>appendRow({rol:roleOptions[0],role:roleOptions[0]});wrap.append(add);
 return wrap;
}
// Formularios institucionales de semanas: etiquetas visibles sin pedir JSON.
// Mantiene intactos los formatos que FastAPI espera (lista para el Anexo 2,
// diccionario para el Anexo 5) y omite únicamente semanas sin contenido.
function weeklyFieldMode(name){
 const path=state.formPath||'';
 if(name==='cronograma'&&(path==='/ciclo-rsu/proyectos'||path==='/ciclo-rsu/proyectos/{project_id}/anexos-1-2'))return 'weekly-plan';
 if(name==='actividades_semanales'&&path==='/ciclo-rsu/proyectos/{project_id}/anexo-5')return 'weekly-progress';
 return '';
}
function weeklyFieldEditor(name,required,value,mode){
 const plan=mode==='weekly-plan',total=16;
 const wrap=mk('section','form-field full weekly-editor');
 wrap.dataset.type=mode;wrap.dataset.key=name;
 const heading=mk('div','weekly-editor-heading');
 heading.append(mk('h3','',plan?'Cronograma de actividades · Semanas 1 a 16':'Informe de avance (Anexo 5) · Semanas 1 a 16'));
 heading.append(mk('p','field-help',plan?
  'Describe las actividades previstas en cada semana del proyecto. Los datos se guardan en el cronograma del Anexo 2.':
  'Describe lo que realmente se realizó en cada semana. Las semanas sin actividades pueden quedar en blanco.'));
 wrap.append(heading);
 const previous=new Map();
 if(Array.isArray(value)){for(const item of value){if(!item||typeof item!=='object')continue;const n=Number(item.semana);if(Number.isInteger(n)&&n>=1&&n<=total)previous.set(n,String(item.actividad||''));}}
 else if(value&&typeof value==='object'){for(const [key,text] of Object.entries(value)){const n=Number(String(key).replace(/^semana[_\s-]*/i,''));if(Number.isInteger(n)&&n>=1&&n<=total)previous.set(n,String(text||''));}}
 const grid=mk('div','weekly-editor-grid');
 for(let week=1;week<=total;week++){
  const row=mk('label','weekly-editor-row');row.dataset.week=String(week);
  row.append(mk('span','weekly-editor-label',`Semana ${week}`));
  const textarea=mk('textarea','input weekly-editor-input');textarea.rows=2;
  textarea.placeholder=plan?`¿Qué se realizará durante la semana ${week}?`:`¿Qué actividades se realizaron en la semana ${week}?`;
  textarea.value=previous.get(week)||'';
  if(plan)textarea.maxLength=2000;
  row.append(textarea);grid.append(row);
 }
 wrap.append(grid);
 wrap.append(mk('p','field-help weekly-editor-footer',plan?
  'Completa el cronograma semana por semana. Se guardarán solo las semanas que tengan una actividad escrita; registra al menos una.':
  'Las semanas 1 a 9 figuran en el formato institucional del Anexo 5. Si registras las semanas 10 a 16, se incluirán en una hoja complementaria del PDF sin alterar la plantilla oficial.'));
 return wrap;
}

function createField(name,schema,required=false,value=undefined){
 const weeklyMode=weeklyFieldMode(name);if(weeklyMode)return weeklyFieldEditor(name,required,value,weeklyMode);
 const s=resolveSchema(schema);const wrap=mk('div','form-field');wrap.dataset.key=name;wrap.dataset.type=s.type||'string';wrap._schema=s;
 const type=s.type||'string';let title=labelOf(name);let fieldLabel=mk('label','field-label');fieldLabel.textContent=title+(required?' *':'');wrap.append(fieldLabel);
 if(isEncodedFile(s)){wrap.dataset.type='file-object';wrap.classList.add('full');const inp=mk('input','input');inp.type='file';inp.accept='.png,.jpg,.jpeg,.pdf,.webp';const l=mk('input','input');l.placeholder='Etiqueta de la firma o evidencia';l.value=value?.label||title;const p=mk('select','input');p.style.marginTop='7px';p.append(new Option('Persona vinculada (opcional)',''));for(const person of state.lookup.personas||[])p.append(new Option(`${person.nombre_completo||'Persona'}${person.dni?' · '+person.dni:''}`,String(person.persona_id)));p.value=value?.person_id||'';const help=mk('small','field-help','Adjunta una firma o documento del expediente. Se incorporará al formulario al guardar.');wrap.append(inp,l,p,help);wrap._file=inp;wrap._label=l;wrap._person=p;wrap._existing=value;return wrap}
 if(type==='object'&&s.additionalProperties&&!s.properties){wrap.dataset.type='dict';wrap.classList.add('full');const ta=mk('textarea','input');ta.rows=5;ta.placeholder='Ingresa un objeto JSON válido; p. ej. {"semana_1": "Actividad realizada"}';ta.value=value?JSON.stringify(value,null,2):'{}';wrap.append(ta);return wrap}
 if(type==='object'){wrap.dataset.type='object';wrap.classList.add('full');const f=mk('fieldset','nested');let l=mk('legend','','Datos de '+title);f.append(l);f.append(fieldsFromSchema(s,value||{}));wrap.innerHTML='';wrap.append(f);return wrap}
 if(type==='array'&&['miembros','colaboradores'].includes(name)&&(resolveSchema(s.items).properties?.persona_id||resolveSchema(s.items).type==='string'))return memberEditor(name,schema,required,value);
 if(type==='array'){wrap.dataset.type='array';wrap.classList.add('full');const list=mk('div','array-list');wrap.append(list);let add=mk('button','btn ghost small','+ Añadir '+title);add.type='button';const appendRow=(val)=>{let item=mk('div','array-row');let top=mk('div','inline-between');let t=mk('span','small-k','Elemento '+(list.children.length+1));let del=mk('button','btn warn small','Quitar');del.type='button';del.onclick=()=>{item.remove();Array.from(list.children).forEach((r,i)=>r.querySelector('.small-k').textContent=`Elemento ${i+1}`)};top.append(t,del);item.append(top);item.append(createField((name==='colaboradores'&&resolveSchema(s.items).type==='string')?'persona_id':'elemento',s.items||{type:'string'},true,val));list.append(item)};
 (Array.isArray(value)?value:(required&&(s.minItems||0)>0?[undefined]:[])).forEach(appendRow);add.onclick=()=>appendRow(undefined);wrap.append(add);return wrap}
 const opts=refDefinition(name);if(opts && !s.enum){let select=mk('select','input');select.append(new Option('Seleccionar '+title, ''));const [key,id,parts]=opts;let rows=(key==='convocatorias'&&!value?(state.lookup.convocatoriasActivas||[]):(state.lookup[key]||[]));if(value&&!rows.some(x=>String(x[id])===String(value)))select.append(new Option('Registro seleccionado (no disponible en el catálogo actual)',String(value)));for(const r of rows){const label=parts.map(p=>r[p]).filter(Boolean).join(' · ')||r.nombre||r.nombre_completo||'Registro sin descripción';select.append(new Option(String(label),String(r[id]||'')))}select.value=value??'';wrap.append(select);return wrap}
 if(s.enum){let select=mk('select','input');select.append(new Option('Seleccionar '+title,''));s.enum.forEach(opt=>select.append(new Option(prettify(opt),opt)));select.value=value??s.default??'';wrap.append(select);return wrap}
 if(type==='boolean'){let select=mk('select','input');select.append(new Option('No','false'));select.append(new Option('Sí','true'));select.value=String(value??s.default??false);wrap.append(select);return wrap}
 if(type==='string'&&name.endsWith('_base64')){wrap.classList.add('full');const file=mk('input','input');file.type='file';file.accept='image/*,.pdf';const hid=mk('textarea','input');hid.hidden=true;hid.value=value||'';file.onchange=async()=>{if(file.files[0])hid.value=await fileAsBase64(file.files[0])};wrap.append(file,hid);return wrap}
 const multiline=/resumen|objetivo|metodologia|problematica|dificultades|logros|recomendaciones|impacto|necesidades|actividades|observaciones|descripcion|respuesta|mensaje|comentario/i.test(name)||((s.maxLength||0)>280);
 let input;if(multiline){input=mk('textarea','input');input.rows=3}else{input=mk('input','input');input.type=s.format==='date'?'date':s.format==='date-time'?'datetime-local':s.format==='password'||name==='password'?'password':type==='integer'||type==='number'?'number':name.includes('email')?'email':'text';if(type==='integer')input.step='1';else if(type==='number')input.step='any';if(s.minLength!==undefined)input.minLength=s.minLength;if(s.maxLength!==undefined)input.maxLength=s.maxLength;if(s.minimum!==undefined)input.min=s.minimum;if(s.maximum!==undefined)input.max=s.maximum;}
 input.placeholder=s.description||title;input.value=value===undefined||value===null?(s.default??''):String(value);if(input.type==='date'&&input.value)input.value=input.value.slice(0,10);if(required)input.required=true;wrap.append(input);if(s.description){const hint=mk('div','field-help',s.description);wrap.append(hint)}if(s.minLength!==undefined){input.minLength=s.minLength;wrap.append(mk('div','field-help',`Mínimo ${s.minLength} caracteres`))}if(multiline||name==='titulo'||name==='resumen')wrap.classList.add('full');return wrap
}
async function fileAsBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('No se pudo leer el archivo.'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file)})}
async function extractField(w){const type=w.dataset.type;const s=w._schema||{};const key=w.dataset.key;
 if(type==='weekly-plan'||type==='weekly-progress'){
  const planned=type==='weekly-plan',rows=$$('.weekly-editor-row',w);
  const result=planned?[]:{};
  for(const row of rows){const week=Number(row.dataset.week),activity=$('textarea',row).value.trim();if(!activity)continue;
   if(planned)result.push({semana:week,actividad:activity});else result[String(week)]=activity;
  }
  if(!Object.keys(result).length)throw new Error(planned?'Registra las actividades de al menos una semana del cronograma.':'Registra las actividades realizadas en al menos una semana del informe de avance.');
  return result;
 }
 if(type==='member-array'){const rows=$$('.member-card',w);const values=[];for(const row of rows){const person=$('.member-person',row)?.value,role=$('.member-role',row)?.value;if(!person)throw new Error('Selecciona a una persona para cada integrante.');if(values.some(x=>x.persona_id===person))throw new Error('Una persona no puede registrarse dos veces en el mismo equipo.');const props=resolveSchema(resolveSchema(w._schema).items).properties||{};let rec=Object.keys(props).length?{persona_id:person}:person;if(typeof rec==='object'){if(props.rol)rec.rol=role;if(props.role)rec.role=role;if(props.horas_semanales){const kind=String((state.lookup.personas||[]).find(x=>String(x.persona_id)===person)?.tipo_persona||'').toLowerCase();const hours=$('.member-hours',row)?.value;if(kind==='docente'){if(!hours)throw new Error('Indica las horas semanales del docente seleccionado.');rec.horas_semanales=Number(hours)}}}values.push(rec)}return values}
 if(type==='array'){const rows=$$('.array-row',w.querySelector('.array-list')); const out=[];for(const row of rows){let v=await extractField(row.querySelector('[data-key="elemento"], [data-key="persona_id"]'));if(v!==undefined)out.push(v)}return out}
 if(type==='object'){return collectFields($('.form-grid',w))}
 if(type==='criteria'){const out={};$$('[data-criterion]',w).forEach(r=>{out[r.dataset.criterion]=$('select',r).value==='true'});return out}
 if(type==='criteria-notes'){const out={};const form=w.parentElement;$$('[data-criterion]',form).forEach(r=>{const note=$('textarea',r).value.trim();if(note)out[r.dataset.criterion]=note});return out}
 if(type==='dict'){const raw=$('textarea',w).value.trim();try{return JSON.parse(raw||'{}')}catch{throw new Error(`El campo ${labelOf(key)} no contiene un JSON válido.`)}}
 if(type==='file-object'){const file=w._file.files[0];if(!file){if(w._existing)return w._existing;return undefined}return {filename:file.name,mime_type:file.type||'application/octet-stream',data_base64:await fileAsBase64(file),label:w._label.value||labelOf(key),person_id:w._person.value||null}}
 if(type==='string'&&key.endsWith('_base64')){let result=$('textarea',w)?.value||'';return result||undefined}
 const control=$('input,textarea,select',w);let raw=control?.value??'';if(raw==='')return undefined;if(type==='boolean')return raw==='true';if(type==='integer')return Number.parseInt(raw,10);if(type==='number')return Number(raw);return raw
}
async function collectFields(container){const data={};for(const w of Array.from(container.children).filter(n=>n.hasAttribute('data-key'))){const v=await extractField(w);if(v!==undefined)data[w.dataset.key]=v}return data}
async function openAction(method,path,pathValues={},initial={},backLabel='',onCompleted=null,correctionIssues=[]){
 if(!state.spec){toast('No se pudo leer el esquema OpenAPI de FastAPI.',true);return}
 const operation=getOperation(method,path);if(!operation){toast('La operación solicitada no existe en el servidor: '+method+' '+path,true);return}
 // Los datos del catálogo pueden cambiar entre sesiones. Antes de abrir un formulario
 // recargamos las referencias principales para mostrar las convocatorias vigentes.
 if(!state.lookup.convocatorias||!state.lookup.lineas||!state.lookup.personas)await loadLookup();
 state.formPath=path;const title=guessTitle(method,path);
 let evaluationCriteria=[];
 try{
  if(path.includes('/anexo-4/evaluar')||path.includes('/anexo-7/evaluar')){
   const c=await api('/ciclo-rsu/catalogo/criterios');
   evaluationCriteria=Object.entries(c[path.includes('anexo-4')?'anexo_4':'anexo_7']||{}).map(([clave,nombre])=>({clave,nombre}));
  }else if(path.includes('/actividades-especificas/')&&(path.endsWith('/evaluar-plan')||path.endsWith('/evaluar-cierre'))){
   const c=await api('/actividades-especificas/catalogo/criterios');
   evaluationCriteria=c[path.endsWith('/evaluar-plan')?'inicial':'final']||[];
   initial={...initial,criterios:evaluationCriteria.map(({clave})=>({clave,cumple:true,observacion:''}))};
  }
 }catch(e){console.warn('Catálogo de criterios no disponible',e.message)}
 state.evalCriteria=evaluationCriteria;
 const opts=operation.parameters||[];
 const requiredPath=opts.filter(x=>x.in==='path'&&!pathValues[x.name]);
 const queryOptions=opts.filter(x=>x.in==='query');
 const bodySpec=getBodySpec(operation);
 const correctionHeader=correctionIssues.length?`<section class="rsu-correction-summary" role="region" aria-label="Incisos que debes subsanar"><h3>Correcciones solicitadas por el evaluador</h3><p>Identifica el inciso observado, revisa lo que se te solicita y corrige el campo resaltado. Al guardar, la propuesta será reenviada para una nueva revisión.</p>${correctionIssues.map((item,i)=>`<div class="rsu-correction-summary-item"><strong>${esc(item.key?'Inciso: '+item.label:'Observación general')}</strong><p>${esc(item.text)}</p>${item.fields.length?`<button type="button" class="btn secondary small" data-correction-jump="${i}">Ir al campo observado</button>`:''}</div>`).join('')}</section>`:'';
 const html=`<form id="operation-form" autocomplete="off"><div class="hint">${esc(operation.description||'Completa los datos de la operación. La API validará el procedimiento, el estado del expediente y tus permisos según el Reglamento de RSU.')}</div>${correctionHeader}<div id="operation-inputs"></div>${bodySpec.hasBody?`<h3 style="margin:24px 0 12px">Datos del formulario</h3><div id="operation-body"></div>`:''}</form>`;
 const currentContext=state.context&&path.includes('{project_id}')?{...state.context}:null;
 openDialog(title,html,btn('Volver','dialog-back','ghost')+`<button type="submit" form="operation-form" class="btn" id="operation-submit">${['DELETE'].includes(method)?'Confirmar eliminación':'Guardar y continuar'}</button>`,()=>currentContext?openEntity(currentContext.type,currentContext.id):(path.startsWith('/personas/registrar/')?openChoosePerson():closeDialog()));
 const paramContainer=$('#operation-inputs');for(const p of [...requiredPath,...queryOptions]){const ps=resolveSchema(p.schema||{type:'string'});paramContainer.append(createField(p.name,ps,p.required||p.in==='path',pathValues[p.name]??initial[p.name]));}
 let form,bodyContainer=$('#operation-body');let hasInlineMembers=false,supportsInlineSignatures=false;
 if(bodySpec.hasBody){form=mk('div','form-grid');form.dataset.role='body-fields';bodyContainer.append(form);const s=bodySpec.schema;hasInlineMembers=Boolean(s.properties?.miembros||s.properties?.colaboradores);supportsInlineSignatures=Boolean(s.properties?.firmas||s.properties?.firma_responsable||s.properties?.firmas_colaboradores);state.formRequiresSignatures=supportsInlineSignatures;
 for(const [k,sub] of Object.entries(s.properties||{})){
  const sample=initial?.[k];let field;
  if(hasInlineMembers&&(k==='firmas'||k==='firmas_colaboradores'||k==='firma_responsable'))continue;
  if(k==='criterios'&&evaluationCriteria.length&&resolveSchema(sub).additionalProperties){
   field=mk('div','form-field full');field.dataset.key=k;field.dataset.type='criteria';
   field.innerHTML='<label class="field-label">Ficha de evaluación oficial (sí / no)</label><p class="field-help">Marca cada criterio. Si indicas NO, describe la observación.</p>';
   for(const criterion of evaluationCriteria){const row=mk('div','file-control');row.dataset.criterion=criterion.clave;const l=mk('label','field-label',criterion.nombre);const sel=mk('select','input');sel.innerHTML='<option value="true">Sí, cumple</option><option value="false">No cumple</option>';sel.value=String(sample?.[criterion.clave]??true);const note=mk('textarea','input');note.rows=2;note.placeholder='Observación de este criterio, si corresponde';note.style.marginTop='7px';note.value=initial?.observaciones_por_criterio?.[criterion.clave]||'';row.append(l,sel,note);field.append(row)}
  }else if(k==='observaciones_por_criterio'&&evaluationCriteria.length){field=mk('div','form-field');field.dataset.key=k;field.dataset.type='criteria-notes';field.hidden=true}
  else field=createField(k,sub,(s.required||[]).includes(k),sample);
  if(bodySpec.contentType?.includes('multipart/form-data')&&resolveSchema(sub).format==='binary'){
   field.innerHTML='';field.dataset.type='binary';const input=mk('input','input');input.type='file';input.required=(s.required||[]).includes(k);field.append(mk('label','field-label',labelOf(k)),input);
  }
  form.append(field);
 }
 if(hasInlineMembers&&supportsInlineSignatures){const own=mk('div','form-field full');own.append(mk('h3','','Firma del responsable'));own.append(mk('p','field-help',`Responsable: ${state.user?.nombre_completo||'Cuenta actual'}. Firma dentro de este mismo formulario.`));const pad=signaturePad(state.user?.nombre_completo||'responsable',state.user?.persona_id||'');pad.dataset.ownSignature='1';own.append(pad);form.append(own)}
 if(path.includes('/voluntariados/')&&path.endsWith('/anexo-12')&&form){try{const projectId=pathValues.project_id;const det=await api(`/voluntariados/${encodeURIComponent(projectId)}`);const stored=det.firmas_previas||[];const personWrap=Array.from(form.children).find(x=>x.dataset?.key==='persona_id');const sigWrap=Array.from(form.children).find(x=>x.dataset?.key==='firma');if(personWrap&&sigWrap){const sel=$('select,input',personWrap);const sync=()=>{const found=stored.find(x=>String(x.persona_firmante_id)===String(sel.value));sigWrap._existing=found?{workflow_file_id:found.workflow_file_id,filename:'firma.png',label:'Firma del voluntario',person_id:found.persona_firmante_id}:null;let hint=$('.reuse-hint',sigWrap);if(!hint){hint=mk('p','field-help reuse-hint');sigWrap.append(hint)}hint.textContent=found?'Firma registrada durante la asignación; puedes reutilizarla o adjuntar una nueva.':'Adjunta o dibuja la firma del voluntario.'};sel.addEventListener('change',sync);sync()}}catch(err){console.warn('Firma del Anexo 12 no disponible',err.message)}}
 if(!s.properties){const ta=mk('textarea','input');ta.rows=8;ta.placeholder='JSON requerido por FastAPI';ta.value=JSON.stringify(initial||{},null,2);ta.id='raw-body';bodyContainer.append(ta)}
 }
 // Resalta y documenta el inciso observado, junto al dato editable que debe corregirse.
 if(correctionIssues.length&&form){
  for(const item of correctionIssues){for(const fieldName of item.fields){const container=Array.from(form.children).find(w=>w.dataset.key===fieldName);if(!container)continue;
   container.classList.add('rsu-correction-field');const inline=mk('div','rsu-correction-field-note');inline.append(mk('strong','',`Observación · ${item.label}`));inline.append(mk('p','',item.text));container.insertBefore(inline,container.firstChild);
  }}
  $$('[data-correction-jump]',$('#dialog-host')).forEach(b=>b.onclick=()=>{
   const issue=correctionIssues[Number(b.dataset.correctionJump)];let target=null;
   for(const key of issue.fields){target=Array.from(form.children).find(w=>w.dataset.key===key);if(target)break;}
   if(!target){toast('Esta observación es general. Revisa el texto y los documentos del expediente.');return;}
   target.scrollIntoView({behavior:'smooth',block:'center'});const ctl=$('textarea,input,select',target);if(ctl)ctl.focus({preventScroll:true});
  });
 }
 $('#operation-form').onsubmit=async(e)=>{e.preventDefault();const submit=$('#operation-submit');submit.disabled=true;submit.textContent='Guardando…';try{
  const params={...pathValues};let query=new URLSearchParams();for(const w of Array.from(paramContainer.children).filter(x=>x.hasAttribute('data-key'))){const val=await extractField(w);if(val!==undefined){const p=opts.find(x=>x.name===w.dataset.key);if(p?.in==='path')params[w.dataset.key]=val;else query.set(w.dataset.key,String(val));}}
  let url=expandPath(path,params);if(query.size)url+=`?${query}`;
  let body=null,multipart=false;
  if(bodySpec.hasBody){if(form){const values=await collectFields(form);if(bodySpec.contentType?.includes('multipart/form-data')){const fd=new FormData();for(const w of form.children){const k=w.dataset.key;const value=values[k];if(w.dataset.type==='binary'){const file=$('input[type=file]',w)?.files[0];if(file)fd.append(k,file)}else if(value!==undefined){fd.append(k,typeof value==='object'?JSON.stringify(value):String(value))}}body=fd;multipart=true}else{body=values;if(hasInlineMembers&&supportsInlineSignatures){const signers=[];for(const member of $$('.member-card',form)){const person=$('.member-person',member)?.value;const label=(state.lookup.personas||[]).find(x=>String(x.persona_id)===person)?.nombre_completo||'integrante';const preExisting=(initial?.firmas||[]).find(x=>String(x.person_id)===String(person)&&x.workflow_file_id);const signature=await readSignature($('.member-signature',member),person,label,!preExisting);if(signature)signers.push(signature);else if(preExisting)signers.push(preExisting)}
 const savedOwn=(initial?.firmas||[]).find(x=>String(x.person_id)===String(state.user?.persona_id)&&x.workflow_file_id);const own=await readSignature($('[data-own-signature]',form),state.user?.persona_id||'',state.user?.nombre_completo||'responsable',!savedOwn)||savedOwn;if(path.includes('/voluntariados/')){body.firma_responsable=own;body.firmas_colaboradores=signers}else if(bodySpec.schema?.properties?.firma_responsable){body.firma_responsable=own;if(bodySpec.schema?.properties?.firmas)body.firmas=signers.filter(x=>String(x.person_id)!==String(state.user?.persona_id))}else{body.firmas=[...signers,own]}}}}else{try{body=JSON.parse($('#raw-body').value)}catch{throw new Error('El contenido JSON no es válido.')}}}
  const res=await api(url,{method,body,multipart,download:false});toast(res?.message||'Operación registrada correctamente.');closeDialog();
  if(typeof onCompleted==='function')return await onCompleted(res);
  await loadLookup();await navigate(state.active);
 }catch(err){toast(err.message,true);submit.disabled=false;submit.textContent='Guardar y continuar'}};
}
async function boot(){if(state.token){try{const userText=sessionStorage.getItem('rsu_user');if(!userText){logout(true);return}state.user=JSON.parse(userText);await api('/flujo-rsu/dashboard');await initialize()}catch(e){console.warn('Sesión no recuperable',e.message);logout(true)}}else renderLogin()}
// La sesión dura mientras la pestaña permanezca abierta.
boot();
