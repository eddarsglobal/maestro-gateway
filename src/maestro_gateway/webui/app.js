'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const state = {token: null, conversationId: null, workspaceId: null, workspaces: [], busy: false, models: [], systemReady: false};
  const SETTINGS_KEY='maestro.desktop.settings.v1';
  function readSettings(){
    const defaults={model:'',maxTokens:1024,theme:'dark'};
    try{
      const saved=JSON.parse(localStorage.getItem(SETTINGS_KEY)||'{}');
      if(!saved||typeof saved!=='object')return defaults;
      return {
        model:typeof saved.model==='string'&&saved.model.length<256?saved.model:'',
        maxTokens:[256,512,1024,2048].includes(saved.maxTokens)?saved.maxTokens:1024,
        theme:['dark','light','system'].includes(saved.theme)?saved.theme:'dark'
      };
    }catch{return defaults;}
  }
  state.settings=readSettings();
  const systemColor=window.matchMedia?.('(prefers-color-scheme: dark)');
  function applyTheme(){
    const theme=state.settings.theme;
    document.body.dataset.theme=theme==='system'?(systemColor?.matches?'dark':'light'):theme;
  }
  applyTheme();
  if(systemColor?.addEventListener)systemColor.addEventListener('change',()=>{if(state.settings.theme==='system')applyTheme();});
  function syncSettingsControls(){
    $('settingsModel').value=state.settings.model;
    if($('settingsModel').value!==state.settings.model)$('settingsModel').value='';
    $('settingsTokens').value=String(state.settings.maxTokens);
    $('settingsTheme').value=state.settings.theme;
  }
  function openSettings(){syncSettingsControls();$('settingsFeedback').textContent='';$('settingsDialog').showModal();}
  function closeSettings(){$('settingsDialog').close();}
  function saveSettings(event){
    event.preventDefault();
    const model=$('settingsModel').value;
    const maxTokens=Number($('settingsTokens').value);
    const theme=$('settingsTheme').value;
    if(![256,512,1024,2048].includes(maxTokens)||!['dark','light','system'].includes(theme)){
      $('settingsFeedback').textContent='Paramètres invalides';return;
    }
    const newSettings={model,maxTokens,theme};
    try{localStorage.setItem(SETTINGS_KEY,JSON.stringify(newSettings));}
    catch{$('settingsFeedback').textContent='Enregistrement local impossible';return;}
    state.settings=newSettings;
    $('model').value=model;
    applyTheme();closeSettings();toast('Settings enregistrés dans ce navigateur');
  }
  async function refreshSettingsProvider(){
    try{
      const data=await request('/providers');
      const ollama=(data.providers||[]).find(p=>p.id==='ollama');
      $('settingsProviderStatus').textContent=ollama?.status==='online'
        ?String((ollama.models||[]).length)+' modèle(s) détecté(s) · prêt'
        :'Ollama indisponible : démarrez Ollama pour utiliser les conversations';
      $('settingsProviderDot').classList.toggle('muted',ollama?.status!=='online');
    }catch(e){$('settingsProviderStatus').textContent='Connexion impossible : '+e.message;$('settingsProviderDot').classList.add('muted');}
  }
  let toastTimer;
  function toast(msg){const node=$('toast');node.textContent=String(msg);node.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.classList.remove('visible'),4500);}
  async function request(path, opts={}){
    const headers={'Accept':'application/json'};
    if(opts.body!==undefined)headers['Content-Type']='application/json';
    if(state.token)headers['X-Maestro-Session']=state.token;
    const response=await fetch(path,{method:opts.method||'GET',headers,body:opts.body===undefined?undefined:JSON.stringify(opts.body),cache:'no-store',credentials:'omit'});
    let data;
    try{data=await response.json();}catch{throw new Error('Réponse incorrecte du Gateway');}
    if(!response.ok){throw new Error(data.message||data.error||'Erreur HTTP '+response.status);}
    return data;
  }
  function message(role,text,pending=false){
    const el=document.createElement('article');el.className='message '+role+(pending?' pending':'');
    const label=document.createElement('div');label.className='role';label.textContent=role==='user'?'Vous':'MAESTRO';
    const body=document.createElement('div');body.className='body';body.textContent=String(text||'');
    el.append(label,body);$('chat').append(el);$('chat').scrollTop=$('chat').scrollHeight;return el;
  }
  function showChat(on){$('chat').hidden=!on;$('welcome').hidden=on;}
  function setActiveChat(id){state.conversationId=id;document.querySelectorAll('.conversation-btn').forEach(node=>node.classList.toggle('active',node.dataset.id===id));}
  function updateWorkspace(){const found=state.workspaces.find(w=>w.id===state.workspaceId);const label=found?found.label:'Sélectionner un dossier';$('workspaceName').textContent=label;$('composerWorkspace').textContent=found?'◎ '+label:'◎ Aucun Workspace';}
  async function refreshConversations(){const data=await request('/conversations');const container=$('conversations');container.replaceChildren();for(const chat of data.conversations||[]){const b=document.createElement('button');b.className='conversation-btn';b.dataset.id=chat.id;b.title=chat.title;b.textContent=chat.title||'Conversation';b.classList.toggle('active',chat.id===state.conversationId);b.addEventListener('click',()=>openConversation(chat.id));container.append(b);}}
  async function openConversation(id){try{const chat=await request('/conversations/'+encodeURIComponent(id));setActiveChat(id);$('currentTitle').textContent=chat.title||'Conversation';$('chat').replaceChildren();for(const item of chat.messages||[])if(item.role==='assistant'||item.role==='user')message(item.role,item.content);showChat(true);}catch(e){toast(e.message);}}
  function newChat(){setActiveChat(null);$('currentTitle').textContent='Nouvelle conversation';$('chat').replaceChildren();showChat(false);$('prompt').focus();}
  async function refreshWorkspaces(){const data=await request('/workspaces');state.workspaces=(data.workspaces||[]).filter(x=>x.exists);if(!state.workspaces.some(w=>w.id===state.workspaceId))state.workspaceId=state.workspaces[0]?.id||null;updateWorkspace();}
  async function pickWorkspace(){if(!state.token){toast('Gateway indisponible');return;}try{const data=await request('/workspaces/choose',{method:'POST',body:{kind:'folder'}});if(data.cancelled){return;}await refreshWorkspaces();const row=data.workspace||data;const id=row.id||row.workspace_id;if(id){state.workspaceId=id;updateWorkspace();}toast('Dossier autorisé');}catch(e){if(String(e.message).toLowerCase().includes('macos')){const path=window.prompt('Chemin absolu du dossier à autoriser :');if(path)await authorizePath(path);}else{toast(e.message);}}}
  async function authorizePath(path){try{const row=await request('/workspaces/open',{method:'POST',body:{path}});await refreshWorkspaces();state.workspaceId=row.id;updateWorkspace();toast('Workspace autorisé');}catch(e){toast(e.message);}}
  async function refreshHealth(){try{const health=await request('/health');state.systemReady=health.status==='READY';$('healthValue').textContent=health.status||'Inconnu';$('gatewayValue').textContent=health.gateway_version||'Connecté';$('coreState').textContent='Core '+(health.status||'Inconnu');$('coreDot').classList.toggle('error',!state.systemReady);}catch(e){state.systemReady=false;$('healthValue').textContent='Indisponible';$('coreState').textContent='Core indisponible';$('coreDot').classList.add('error');toast(e.message);}}
  async function refreshModels(){try{const data=await request('/models');const models=data.models||[];state.models=models;$('model').replaceChildren();$('settingsModel').replaceChildren();for(const target of [$('model'),$('settingsModel')])target.append(new Option('Automatique',''));for(const m of models){const name=typeof m==='string'?m:m.name||m.model||m.id;if(name){$('model').append(new Option(name,name));$('settingsModel').append(new Option(name,name));}}const defaultModel=state.settings.model;if(defaultModel&&models.some(m=>(typeof m==='string'?m:m.name||m.model||m.id)===defaultModel)){$('model').value=defaultModel;}else{$('model').value='';}const available=models.length>0;$('modelState').textContent=available?models.length+' modèle(s) local(aux)':'Aucun modèle Ollama';$('providerValue').textContent=available?'Ollama prêt':String(data.status||'Non disponible');$('modelDot').classList.toggle('muted',!available);}catch(e){$('modelState').textContent='Modèles indisponibles';$('providerValue').textContent='Erreur';}}
  async function refreshChanges(){try{const data=await request('/changes');const parent=$('changeList');parent.replaceChildren();const rows=(data.changes||[]).slice(0,20);if(!rows.length){const p=document.createElement('p');p.className='muted-text';p.textContent='Aucune proposition en attente.';parent.append(p);return;}for(const change of rows){const row=document.createElement('div');row.className='change';const title=document.createElement('strong');title.textContent=(change.operation||'changement')+' · '+(change.path||change.relative_path||change.change_id||'');const label=document.createElement('span');label.className='status';label.textContent=change.status||'?';row.append(title,label);if(change.diff){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Vérifier le diff';const pre=document.createElement('pre');pre.className='change-diff';pre.textContent=String(change.diff).slice(0,16000);details.append(summary,pre);row.append(details);}const id=change.change_id||change.id;if(id&&['AWAITING_APPROVAL','APPROVED'].includes(change.status)){const action=change.status==='APPROVED'?'apply':'approve';const b=document.createElement('button');b.textContent=action==='approve'?'Approuver':'Appliquer';b.className=action==='apply'?'danger':'';b.addEventListener('click',async()=>{const intent=action==='approve'?'APPROUVER':'APPLIQUER';if(!window.confirm(intent+' la modification '+id+' ? Vérifiez le contenu et le chemin avant de continuer.'))return;try{await request('/changes/'+encodeURIComponent(id)+'/'+action,{method:'POST',body:{confirm_delete:change.operation==='delete'}});await refreshChanges();toast('Action '+action+' terminée');}catch(e){toast(e.message);}});row.append(b);}parent.append(row);}}catch(e){$('changeList').textContent='Propositions indisponibles : '+e.message;}}
  async function sendMessage(event){event.preventDefault();if(state.busy)return;const prompt=$('prompt').value.trim();if(!prompt)return;state.busy=true;$('send').disabled=true;$('feedback').textContent='Mission en cours…';$('prompt').value='';showChat(true);message('user',prompt);const waiting=message('assistant','MAESTRO analyse votre demande…',true);try{const result=await request('/mission',{method:'POST',body:{prompt,mode:'M0_DIRECT',model:$('model').value||null,conversation_id:state.conversationId,workspace_id:state.workspaceId,max_tokens:state.settings.maxTokens}});if(result.conversation_id)setActiveChat(result.conversation_id);$('currentTitle').textContent=result.conversation_title||'Mission';if(!result.ok)throw new Error(result.error||'Mission non aboutie');waiting.querySelector('.body').textContent=result.output||'Aucune réponse produite.';waiting.classList.remove('pending');await Promise.allSettled([refreshConversations(),refreshChanges()]);}catch(e){waiting.querySelector('.body').textContent='Mission interrompue : '+e.message;waiting.classList.remove('pending');toast(e.message);}finally{state.busy=false;$('send').disabled=false;$('feedback').textContent='';$('prompt').focus();}}
  async function init(){
    $('newChat').addEventListener('click',newChat);$('reloadChats').addEventListener('click',()=>refreshConversations().catch(e=>toast(e.message)));$('chooseWorkspace').addEventListener('click',pickWorkspace);$('openWorkspace').addEventListener('click',pickWorkspace);$('refreshHealth').addEventListener('click',()=>refreshHealth());$('refreshChanges').addEventListener('click',refreshChanges);$('composer').addEventListener('submit',sendMessage);$('openSettings').addEventListener('click',openSettings);$('openSettingsTop').addEventListener('click',openSettings);$('closeSettings').addEventListener('click',closeSettings);$('cancelSettings').addEventListener('click',closeSettings);$('settingsForm').addEventListener('submit',saveSettings);$('checkModels').addEventListener('click',async()=>{await Promise.allSettled([refreshModels(),refreshSettingsProvider()]);syncSettingsControls();toast('État Ollama actualisé');});$('settingsDialog').addEventListener('click',e=>{if(e.target===$('settingsDialog'))closeSettings();});
    $('prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}});
    document.querySelectorAll('[data-prompt]').forEach(button=>button.addEventListener('click',()=>{$('prompt').value=button.dataset.prompt;$('prompt').focus();}));
    try{const session=await request('/session',{method:'POST',body:{}});state.token=session.session_token;if(!state.token)throw Error('Session indisponible');await Promise.allSettled([refreshHealth(),refreshModels(),refreshSettingsProvider(),refreshWorkspaces(),refreshConversations(),refreshChanges()]);}catch(e){$('coreState').textContent='Gateway inaccessible';$('coreDot').classList.add('error');toast(e.message);}
  }
  init();
})();
