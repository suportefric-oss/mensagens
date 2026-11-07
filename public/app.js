'use strict';
const $=s=>document.querySelector(s), node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
let csrf='',state=null,tab='compose',selected=new Set(),draft=null,polling=false,toastTimer,autoSelectedScan=null,lastDestinationRevision='';
const names={compose:['Nova publicação','Sua próxima mensagem.','Prepare o conteúdo e escolha onde ele vai aparecer.'],destinations:['Destinos','Escolha onde publicar.','Grupos e canais públicos ou privados, sem conversas individuais.'],activity:['Atividade','Cada envio, acompanhado.','Veja o resultado de cada destino da última publicação.'],connection:['Conexão da conta','Seu Telegram, conectado.','Conecte sua própria conta para carregar os destinos disponíveis.']};
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,6500)}
async function api(path,body){const r=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Panel-Request':'1','X-CSRF-Token':csrf},...(body===undefined?{}:{body:JSON.stringify(body)})});let b;try{b=await r.json()}catch{throw Error('O servidor não respondeu. Tente novamente.')}if(!r.ok){if(r.status===401&&path!=='login'){showLogin()}throw Error(b.error||'Não foi possível concluir.')}if(b.csrf)csrf=b.csrf;return b}
async function busy(button,fn){const old=button.textContent;button.disabled=true;button.textContent='Aguarde…';try{return await fn()}catch(e){toast(e.message)}finally{button.disabled=false;button.textContent=old}}
function showLogin(){$('#login-screen').hidden=false;$('#shell').hidden=true;state=null;csrf='';autoSelectedScan=null;lastDestinationRevision='';selected.clear();$('#message').value='';$('#two-factor').value='';$('#code').value='';$('#confirm-dialog').close();draft=null}
function showPanel(){$('#login-screen').hidden=true;$('#shell').hidden=false}
function switchTab(next){tab=next;document.querySelectorAll('.tab').forEach(n=>n.hidden=true);$('#'+(next==='destinations'?'compose':next)).hidden=false;$('.compose-grid').hidden=next==='destinations';document.querySelectorAll('[data-tab]').forEach(n=>n.classList.toggle('active',n.dataset.tab===next));$('#breadcrumb').textContent=names[next][0];$('#page-title').textContent=names[next][1];$('#page-description').textContent=names[next][2]}
function updateConnection(){const connected=state.connected;$('#connection-badge').textContent=connected?'● Conectado':'● Desconectado';$('#connection-badge').classList.toggle('connected',connected);$('#account-name').textContent=connected?state.account.name:'Conta desconectada';$('#account-state').textContent=connected?(state.account.username?'@'+state.account.username:'Sessão ativa'):'Conecte para começar';$('#account-avatar').textContent=connected?state.account.name.slice(0,1).toUpperCase():'T';$('#connected-name').textContent=connected?state.account.name:'';$('#connected-details').hidden=!connected;$('#connect-forms').hidden=connected;$('#phone-form').hidden=state.login_stage!=='phone';$('#code-form').hidden=state.login_stage!=='code';$('#two-factor-form').hidden=state.login_stage!=='password';$('#stats-interval').textContent=state.interval+' s';$('#scan').disabled=!connected||state.scan?.status==='running';$('#stop-scan').hidden=state.scan?.status!=='running'}
function updateSelection(){const n=selected.size;$('#stats-selected').textContent=n;$('#selection-count').textContent=n+' destino'+(n===1?'':'s');$('#review').disabled=!state?.connected||n===0||!$('#message').value.trim()||state?.scan?.status==='running'||['running','stopping'].includes(state?.job?.status)}
function renderDestinations(){
 const list=$('#destination-list');list.replaceChildren();
 const search=$('#search').value.toLocaleLowerCase('pt-BR'),type=$('#type-filter').value;
 const all=state.destinations||[],scan=state.scan||{};
 const validIds=new Set(all.map(d=>d.id));selected=new Set([...selected].filter(id=>validIds.has(id)));
 if(scan.status==='completed'&&scan.id!==autoSelectedScan){selected=new Set(validIds);autoSelectedScan=scan.id}
 $('#nav-count').textContent=all.length;$('#stats-destinations').textContent=all.length;
 $('#copy-links').disabled=!all.some(d=>d.link);$('#download-links').disabled=!all.some(d=>d.link);
 $('#scan-info').textContent=scan.status==='running'?
   'Varredura automática em andamento · '+scan.checked_dialogs+' conversas verificadas · '+scan.checked_topics+' tópicos · '+all.length+' destinos encontrados. '+scan.note:
   (scan.note||'Conecte sua conta. A lista será preenchida automaticamente.')+
   (state.scan_time?' Atualizado em '+new Date(state.scan_time*1000).toLocaleString('pt-BR')+'.':'');
 $('#scan-info').classList.toggle('scan-warning',['partial','error','cancelled'].includes(scan.status));
 const warnings=scan.warnings||[];$('#scan-warnings').hidden=!warnings.length;
 $('#scan-warning-count').textContent=warnings.length+' itens não verificados';
 $('#scan-warning-list').replaceChildren(...warnings.map(w=>node('li',w.title+': '+w.message)));
 const visible=all.filter(d=>(d.title+' '+(d.link||'')).toLocaleLowerCase('pt-BR').includes(search)&&(type==='all'||d.kind===type));
 if(!visible.length)list.append(node('div',scan.status==='running'?'Procurando grupos, canais e tópicos, inclusive nos arquivados…':all.length?'Nenhum destino corresponde à busca.':state.connected?'A varredura não encontrou destinos disponíveis. Confira os avisos e atualize a lista.':'Conecte sua conta em “Conexão da conta”.','empty-state'));
 for(const d of visible){
  const row=node('div',undefined,'destination-row');const label=node('label',undefined,'destination-choice');
  const check=node('input');check.type='checkbox';check.checked=selected.has(d.id);check.onchange=()=>{if(check.checked)selected.add(d.id);else selected.delete(d.id);updateSelection()};
  const avatar=node('span',d.kind==='channel'?'↗':'#','avatar'),info=node('span',undefined,'target-info');
  info.append(node('strong',d.title),node('small',d.visibility+(d.archived?' · Arquivado':'')+(d.topic_id?' · Tópico':'')+(d.link_kind==='internal'?' · Link para membros':'')));
  label.append(check,avatar,info);row.append(label,node('span',d.kind==='channel'?'Canal':d.topic_id?'Tópico':'Grupo','kind'));
  if(d.link){const a=node('a','Abrir ↗','destination-link');try{const url=new URL(d.link);if(url.protocol==='https:'&&['t.me','telegram.me'].includes(url.hostname)){a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';a.title=d.link;row.append(a)}}catch{}}
  else row.append(node('span','Sem link disponível','no-link'));
  list.append(row)
 }
 updateSelection()
}
function availableLinks(){return [...new Set((state.destinations||[]).filter(d=>d.link).map(d=>d.link))]}

const statuses={pending:'Pendente',sending:'Enviando',sent:'Enviado',failed:'Falhou',skipped:'Ignorado',uncertain:'Confira no Telegram',blocked:'Restrição'};
const jobNames={running:'Em andamento',stopping:'Parando',cancelled:'Parado',completed:'Concluído',paused:'Pausado pelo Telegram',interrupted:'Interrompido',restricted:'Restrição do Telegram'};
function renderJob(){const j=state.job,items=$('#job-items');items.replaceChildren();if(!j){$('#job-summary').textContent='Nenhuma publicação iniciada.';$('#cancel').hidden=true;$('#resume').hidden=true;$('#progress').value=0;$('#job-note').textContent='';return}const sent=j.items.filter(i=>i.status==='sent').length,done=j.items.filter(i=>!['pending','sending'].includes(i.status)).length;$('#job-summary').textContent=jobNames[j.status]+' · '+sent+' de '+j.items.length+' enviados';$('#progress').max=j.items.length||1;$('#progress').value=done;$('#job-note').textContent=j.note+(j.wait_until>Date.now()/1000?' · Pode continuar após '+new Date(j.wait_until*1000).toLocaleTimeString('pt-BR'):'');$('#cancel').hidden=!['running','stopping','paused','interrupted'].includes(j.status);$('#resume').hidden=!['paused','interrupted'].includes(j.status);$('#resume').disabled=j.wait_until>Date.now()/1000;for(const item of j.items){const row=node('div',undefined,'job-row');const text=node('div',item.title);text.append(node('small',item.detail||'Aguardando na fila'));row.append(text,node('span',statuses[item.status]||item.status,'status-label '+item.status));items.append(row)}updateSelection()}
async function refresh(){const next=await api('status');state=next;showPanel();updateConnection();renderJob();const revision=JSON.stringify([state.connected,state.scan,state.destinations]);if(revision!==lastDestinationRevision){lastDestinationRevision=revision;renderDestinations()}updateSelection()}
async function restore(){try{await api('restore',{})}catch(e){toast(e.message)}await refresh();renderDestinations();if(state.connected&&tab==='connection')switchTab('compose')}
$('#owner-form').onsubmit=async e=>{e.preventDefault();$('#login-error').textContent='';const button=e.submitter;await busy(button,async()=>{try{await api('login',{password:$('#owner-password').value});$('#owner-password').value='';await refresh();renderDestinations();switchTab('connection');await restore()}catch(e){$('#login-error').textContent=e.message}})};
document.querySelector('nav').onclick=e=>{const b=e.target.closest('[data-tab]');if(b)switchTab(b.dataset.tab)};
for(const id of ['#logout','#mobile-logout'])$(id).onclick=()=>busy($(id),async()=>{await api('logout',{});showLogin();toast('Painel fechado. Um envio iniciado continua no servidor.')});
function loginForm(id,action,payload,clear){$(id).onsubmit=async e=>{e.preventDefault();$('#connection-error').textContent='';await busy(e.submitter,async()=>{try{const b=await api(action,payload());clear();await refresh();if(b.stage==='connected'){toast('Conta conectada. A varredura automática começou.');switchTab('compose');renderDestinations()}}catch(e){clear();$('#connection-error').textContent=e.message;await refresh()}})}}
loginForm('#phone-form','request-code',()=>({phone:$('#phone').value.replace(/[\s()\-]/g,'')}),()=>{});
loginForm('#code-form','verify-code',()=>({code:$('#code').value.trim()}),()=>$('#code').value='');
loginForm('#two-factor-form','verify-password',()=>({password:$('#two-factor').value}),()=>$('#two-factor').value='');
$('#new-code').onclick=()=>{state.login_stage='phone';updateConnection()};
$('#disconnect').onclick=()=>{if(confirm('Encerrar a sessão Telegram deste painel? Um envio ativo será parado.'))busy($('#disconnect'),async()=>{await api('disconnect',{});selected.clear();await refresh();renderDestinations();toast('Conta desconectada.')})};
$('#scan').onclick=()=>busy($('#scan'),async()=>{await api('scan',{});await refresh();toast('Varredura iniciada. O painel acompanha o progresso automaticamente.')});
$('#stop-scan').onclick=()=>busy($('#stop-scan'),async()=>{await api('cancel-scan',{});await refresh();toast('Varredura interrompida. A lista pode estar incompleta.')});
$('#copy-links').onclick=()=>busy($('#copy-links'),async()=>{await navigator.clipboard.writeText(availableLinks().join('\n'));toast('Links disponíveis copiados. Links internos exigem participação no grupo.')});
$('#download-links').onclick=()=>{const text=(state.destinations||[]).filter(d=>d.link).map(d=>d.title+'\n'+d.link).join('\n\n');const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));const a=node('a');a.href=url;a.download='links-telegram.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};

$('#search').oninput=renderDestinations;$('#type-filter').onchange=renderDestinations;
$('#select-all').onclick=()=>{state.destinations.forEach(d=>selected.add(d.id));renderDestinations();toast('Todos os destinos disponíveis foram selecionados, inclusive os ocultos pelo filtro.')};$('#clear-all').onclick=()=>{selected.clear();renderDestinations()};
$('#message').oninput=()=>{const text=$('#message').value;$('#char-count').textContent=text.length.toLocaleString('pt-BR')+' / 4.096';const preview=$('#message-preview');preview.replaceChildren(document.createTextNode(text||'Sua mensagem aparecerá aqui.'),node('small','Agora ✓✓'));updateSelection()};
$('#review').onclick=()=>busy($('#review'),async()=>{draft=await api('prepare',{message:$('#message').value,ids:[...selected]});$('#confirm-description').textContent='Publicar pela conta '+state.account.name+' em '+draft.destinations.length+' destinos, com intervalo de '+draft.interval+' segundos.';$('#confirm-message').textContent=draft.message;$('#confirm-count').textContent='Ver os '+draft.destinations.length+' destinos';$('#confirm-destinations').replaceChildren(...draft.destinations.map(d=>node('li',d.title+' · '+(d.kind==='channel'?'Canal':'Grupo'))));$('#confirm-dialog').showModal()});
$('#confirm-send').onclick=async e=>{e.preventDefault();await busy($('#confirm-send'),async()=>{if(!draft)return;await api('start',{ticket:draft.ticket,confirmed:true});draft=null;$('#confirm-dialog').close();await refresh();switchTab('activity');toast('Envio iniciado. Acompanhe os resultados por destino.')})};
$('#cancel').onclick=()=>busy($('#cancel'),async()=>{await api('cancel',{});await refresh();toast('Parada solicitada. A mensagem em andamento ainda pode ser enviada.')});
$('#resume').onclick=()=>{if(confirm('Continuar somente os destinos pendentes? Resultados incertos não serão repetidos.'))busy($('#resume'),async()=>{await api('resume',{});await refresh()})};
setInterval(async()=>{if(!state||polling||document.hidden)return;polling=true;try{await refresh()}catch(e){toast(e.message)}finally{polling=false}},3000);
(async()=>{try{await refresh();renderDestinations();switchTab(state.connected?'compose':'connection');await restore()}catch{showLogin()}})();
