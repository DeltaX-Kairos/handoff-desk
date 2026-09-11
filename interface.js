const $=selector=>document.querySelector(selector);
const token=$('meta[name="handoff-token"]').content;
const result=$('#result'),badge=$('#badge'),exportButton=$('#export-button');
let busy=false,definitionsEdited=false,definitionsAcknowledged=false,investigationEnabled=false,currentState=null;
function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function controls(disabled){
  document.querySelectorAll('button,input,textarea').forEach(x=>x.disabled=disabled);
  if(!disabled){$('#investigate-button').disabled=!investigationEnabled;$('#agent-prompt').disabled=!investigationEnabled;$('#confirm-definitions').disabled=!currentState?.choices['clean-data']||!definitionsAcknowledged;}
}
function candidateCaption(file){
  if(file==='final.csv')return 'Wrong month; delivery requires 2026-09';
  if(file==='final_v2.csv')return 'Matches required reporting period 2026-09';
  return 'Candidate delivery version';
}
function definitionPlaceholder(header){
  if(header==='id')return 'client account code, keep leading zeros.';
  if(header==='period')return 'reporting month, e.g. 2026-09.';
  if(header==='amount')return 'amount in the agreed currency unit.';
  return 'What does this column represent?';
}
function renderProject(state){
  currentState=state;investigationEnabled=state.investigation_enabled===true;definitionsAcknowledged=false;
  const requirement=state.checklist[0],chosen=state.choices['clean-data'];
  $('#dataset-label').textContent=state.dataset==='uploaded'?'Your selected CSV files':'Original fictional files · Demo';
  $('#session-note').textContent=state.hosted?'Private visitor session. Files expire after one hour of inactivity; a server restart clears access.':state.persistent?(state.restore_status==='restored'?'Saved choices restored. Run the delivery check again.':state.restore_status==='invalidated'?'Files or saved state changed. Confirm your choices again.':'Choices are saved on this Mac.'):'Temporary session — choices clear when the server stops.';
  $('#agent-status').textContent=investigationEnabled?'AWS investigation enabled. Selected file contents may be sent to AWS; requests use account credits.':'Live model not connected. Delivery checks are available.';
  const list=$('#file-list');list.replaceChildren();
  const candidates=state.files.filter(file=>requirement.candidates.includes(file.file));
  const references=state.files.filter(file=>!requirement.candidates.includes(file.file));
  for(const file of [...candidates,...references]){
    const candidate=requirement.candidates.includes(file.file),card=element(candidate?'label':'div',undefined,candidate?'file':'reference');
    if(candidate)card.classList.add(file.file==='final.csv'?'wrong-period':'matches-period');
    if(candidate){const radio=element('input');radio.type='radio';radio.name='version';radio.value=file.file;radio.checked=file.file===chosen;radio.addEventListener('change',()=>act('choose',{file:file.file}));card.append(radio);}
    const description=element('span');description.append(element('b',file.file),element('small',file.row_count+' rows · '+file.headers.length+' columns'));if(candidate)description.append(element('em',candidateCaption(file.file),'caption'));card.append(description);if(!candidate)card.append(element('span','Reference / additional output'));list.append(card);
  }
  const preview=state.files.find(x=>x.file===chosen)||state.files.find(x=>requirement.candidates.includes(x.file));
  const table=$('#file-preview');table.replaceChildren();
  if(preview){$('#preview-title').textContent='Preview: '+preview.file;const head=element('thead'),tr=element('tr');for(const header of preview.headers)tr.append(element('th',header));head.append(tr);table.append(head);const body=element('tbody');for(const row of preview.sample_rows){const line=element('tr');for(const header of preview.headers){const value=String(row[header]??'');line.append(element('td',value.length>500?value.slice(0,500)+'…':value));}body.append(line);}table.append(body);}
  const requirements=$('#requirements');requirements.replaceChildren(element('li','Required columns: '+requirement.required_columns.join(', ')),element('li','Confirmed definitions for every observed column'));
  if(requirement.period)requirements.append(element('li',requirement.period.column+' must equal '+requirement.period.value));
  if(requirement.row_accounting)requirements.append(element('li','Every source ID occurrence accounted for across the explicit outputs'));
  const fields=$('#definition-fields');fields.replaceChildren();
  if(!chosen)fields.append(element('p','Choose a delivery version first.','note'));
  else {for(const header of preview.headers){const label=element('label',header),input=element('input');input.name=header;input.maxLength=4096;input.required=true;input.placeholder=definitionPlaceholder(header);const definitions=state.definitions[chosen]||{};input.value=Object.hasOwn(definitions,header)?definitions[header]:'';label.append(input);fields.append(label);}const ack=element('label',undefined,'definition-ack'),checkbox=element('input'),copy=element('span','I confirm these meanings for this selected delivery.');checkbox.type='checkbox';checkbox.id='definition-ack';checkbox.addEventListener('change',()=>{definitionsAcknowledged=checkbox.checked;controls(false);});ack.append(checkbox,copy);fields.append(ack);}
}
function renderReview(review){
  result.replaceChildren();result.className=review.status==='supported'?'ready':'error';badge.textContent=review.status==='supported'?'Ready to package':'Needs attention';
  for(const item of review.items){
    if(item.status!=='supported'&&item.evidence?.period){
      const period=item.evidence.period,focus=element('div',undefined,'failure-focus');
      focus.append(element('strong','Period mismatch — check delivery first'));
      focus.append(element('div','Contract requires '+period.column+' = '+period.expected+', but '+item.file+' contains '+(period.observed||[]).join(', ')+'.'));
      const rows=period.mismatched_rows||[];if(rows.length){const key=Object.hasOwn(rows[0],'id')?'id':Object.keys(rows[0])[0];focus.append(element('div','Failing cells: '+rows.slice(0,3).map(row=>'('+String(row[key]??'')+', '+String(row[period.column]??'')+')').join(' · ')));}
      result.append(focus);
    }
    const message=item.status==='supported'?'Checked: required columns, confirmed meanings'+(item.requirement.period?', required period':'')+(item.requirement.row_accounting?', source ID occurrences':'')+'.':item.reason||(item.problems||[]).join(' · ');result.append(element('p',message));
  }
}
async function readState(){const response=await fetch('/state');const data=await response.json();if(!response.ok)throw Error(data.error||'The local session could not load.');return data;}
async function act(action,details={}){
  if(busy)return;busy=true;controls(true);exportButton.disabled=true;$('#download').hidden=true;let ready=false;
  if(action==='investigate')$('#investigation-result').textContent='Investigating the selected evidence…';
  try{
    const response=await fetch('/action',{method:'POST',headers:{'Content-Type':'application/json','X-Handoff-Token':token},body:JSON.stringify({action,...details})});const data=await response.json();if(!response.ok)throw Error(data.error||'The operation could not finish.');
    if(action==='new_project'){definitionsEdited=false;renderProject(data);result.textContent='Files loaded. Select the delivery version and confirm its column meanings.';result.className='';badge.textContent='Not checked';$('#investigation-result').textContent='';$('.intake').open=false;}
    else if(action==='investigate'){definitionsAcknowledged=false;$('#investigation-result').textContent=data.message+'\n\nModel calls used in this session: '+data.attempted_model_calls;result.textContent='Review the investigation, record any confirmations, then run the delivery check.';badge.textContent='Recheck required';}
    else if(action==='export'){if(!data.exported)throw Error('Run the delivery check before export.');$('#download').hidden=false;result.textContent='Package prepared. The delivery email is included as an unsent draft.';result.className='ready';ready=true;}
    else{if(action==='choose'||action==='definitions'){definitionsEdited=false;renderProject(await readState());}renderReview(data);ready=data.status==='supported'&&!definitionsEdited;if(definitionsEdited){badge.textContent='Confirm edited definitions';result.textContent='Confirm your edited definitions before preparing a new package.';result.className='error';}}
  }catch(error){result.textContent=error.message;result.className='error';badge.textContent='Needs attention';if(action==='investigate')$('#investigation-result').textContent=error.message;}
  finally{controls(false);exportButton.disabled=!ready;busy=false;}
}
$('#review-button').addEventListener('click',()=>act('review'));
$('#definitions').addEventListener('submit',event=>{event.preventDefault();act('definitions',{meanings:Object.fromEntries(new FormData(event.target))});});
$('#definition-fields').addEventListener('input',()=>{definitionsEdited=true;definitionsAcknowledged=false;const ack=$('#definition-ack');if(ack)ack.checked=false;exportButton.disabled=true;$('#download').hidden=true;badge.textContent='Confirm edited definitions';result.textContent='Your edited definitions are not confirmed yet.';result.className='error';controls(false);});
$('#export-button').addEventListener('click',()=>act('export'));
$('#investigation').addEventListener('submit',event=>{event.preventDefault();if(investigationEnabled)act('investigate',{prompt:$('#agent-prompt').value});});
$('#intake-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;
  try{const files=[...$('#upload-files').files];if(files.length<1||files.length>8||files.some(x=>x.size>512*1024)||files.reduce((n,x)=>n+x.size,0)>2*1024*1024)throw Error('Choose 1–8 CSV files, at most 512 KB each and 2 MB total.');
    const decoder=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true});
    const project={files:await Promise.all(files.map(async file=>({name:file.name,content:decoder.decode(await file.arrayBuffer())}))),required_columns:$('#required-columns').value.split(',').map(x=>x.trim())};
    const column=$('#period-column').value.trim(),value=$('#period-value').value.trim();if(column||value){if(!column||!value)throw Error('Provide both the period column and required value, or leave both empty.');project.period={column,value};}
    await act('new_project',{project});
  }catch(error){result.textContent=error.message;result.className='error';}
});
(async()=>{busy=true;controls(true);try{renderProject(await readState());controls(false);if(currentState.choices['clean-data']){badge.textContent='Recheck required';result.textContent='Confirmed choices loaded. Run the delivery check before exporting.';}}catch(error){result.textContent=error.message;result.className='error';}finally{exportButton.disabled=true;busy=false;}})();
