'use strict';
const $ = id => document.getElementById(id);
let records = [], image = null, start = null, noticeTimer;
const canvas = $('editor'), ctx = canvas.getContext('2d');
function toast(message) { $('notice').textContent = message; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => $('notice').textContent = '', 5000); }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || '请求失败'); return result;
}
function el(tag, text, className) { const n = document.createElement(tag); if (text) n.textContent = text; if(className) n.className = className; return n; }
function card(r, admin = false) {
  const node = el('article', '', 'card'), photo = el('img'); photo.src = '/api/photos/' + encodeURIComponent(r.id); photo.alt = '已遮挡的事件现场照片'; photo.loading = 'lazy'; node.append(photo);
  const body = el('div','','card-body'); body.append(el('span',r.status,'badge'), el('h3',r.kind), el('p',[r.building,r.floor,r.side,r.machine].filter(Boolean).join(' · ')),el('p',r.description),el('small',`${r.occurred.replace('T',' ')} · 上传者：匿名`),el('br'),el('small',`事件编号 ${r.id}`));
  if(admin) {
    body.append(el('p',`仅管理员可见：${r.reporter_building} / ${r.room}`,'private'));
    const label = el('label','','check'), check = el('input'); check.type = 'checkbox'; label.append(check,el('span','已核实事实、场所规则，照片和文字均不含个人身份信息')); body.append(label);
    const actions = el('div','','actions');
    for(const status of ['已发布','处理中','已处理','已驳回','待核实']) { const b = el('button',status); b.onclick = async () => { try { await api('/api/moderate',{id:r.id,status,reviewed:check.checked}); await loadAdmin(); await load(); } catch(e) { toast(e.message); } }; actions.append(b); }
    const remove = el('button','永久删除'); remove.onclick = async () => { if(!confirm('永久删除此事件及照片、上传者信息？')) return; try {await api('/api/delete',{id:r.id}); await loadAdmin(); await load();}catch(e){toast(e.message);} }; actions.append(remove); body.append(actions);
  }
  node.append(body); return node;
}
function render() {
  const filtered = records.filter(r => (!$('typeFilter').value || r.kind === $('typeFilter').value) && r.building.includes($('buildingFilter').value.trim()) && r.floor.includes($('floorFilter').value.trim()) && (!$('statusFilter').value || r.status === $('statusFilter').value));
  $('count').textContent = `${filtered.length} 条已审核记录`;
  $('cards').replaceChildren(...filtered.map(r => card(r)));
  if(!filtered.length) {const empty = el('div','','empty'); empty.append(el('strong','这里还很安静'),el('p','暂无符合条件的公开事件。新反馈审核通过后会显示在这里。')); $('cards').append(empty);}
}
async function load() { records = await api('/api/reports'); render(); }
async function loadAdmin(){const rows = await api('/api/admin'); $('adminPanel').hidden = false; $('adminCards').replaceChildren(...rows.map(r=>card(r,true))); if(!rows.length)$('adminCards').append(el('p','暂无待处理事件'));}
for(const id of ['typeFilter','buildingFilter','floorFilter','statusFilter']) $(id).addEventListener('input',render);
$('new').onclick = () => $('formDialog').showModal(); $('closeForm').onclick = () => $('formDialog').close();
$('browse').onclick = () => $('board').scrollIntoView({behavior:'smooth'});
$('kind').onchange = () => { const laundry = $('kind').value === '洗衣机使用问题'; $('sideLabel').hidden = $('machineLabel').hidden = !laundry; };
$('admin').onclick = async () => { try { await loadAdmin(); $('adminPanel').scrollIntoView({behavior:'smooth'}); } catch(e) { $('loginDialog').showModal(); } };
$('closeLogin').onclick = () => $('loginDialog').close();
$('loginForm').onsubmit = async e => { e.preventDefault(); try { await api('/api/login',{password:$('password').value}); $('password').value = ''; await loadAdmin(); $('loginDialog').close(); $('loginMessage').textContent=''; $('adminPanel').scrollIntoView(); } catch(err) { $('loginMessage').textContent=err.message; } };
$('logout').onclick = async () => {try{await api('/api/logout',{});$('adminPanel').hidden=true;$('adminCards').replaceChildren();}catch(e){toast(e.message);}};
$('photo').onchange = async () => {
  image = null; canvas.hidden = true; $('resetPhoto').hidden = true;
  const file = $('photo').files[0]; if(!file) return;
  if(file.size > 15 * 1024 * 1024) {toast('请选择 15 MB 以内的照片'); $('photo').value='';return;}
  const url = URL.createObjectURL(file), img = new Image();
  try { img.src = url; await img.decode(); const scale = Math.min(1,1600 / Math.max(img.naturalWidth,img.naturalHeight)); canvas.width = Math.round(img.naturalWidth*scale); canvas.height = Math.round(img.naturalHeight*scale); image = img; drawOriginal(); canvas.hidden = false; $('resetPhoto').hidden = false; } catch(e) {toast('无法读取照片，请使用 JPEG、PNG 或 WebP');$('photo').value='';} finally { URL.revokeObjectURL(url); }
};
function drawOriginal(){ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);}
$('resetPhoto').onclick=()=>{if(image)drawOriginal();};
function point(e){const box=canvas.getBoundingClientRect();return [(e.clientX-box.left)*canvas.width/box.width,(e.clientY-box.top)*canvas.height/box.height];}
canvas.onpointerdown=e=>{start=point(e);canvas.setPointerCapture(e.pointerId);};
canvas.onpointerup=e=>{if(!start)return;const end=point(e);ctx.fillStyle='#26352e';ctx.fillRect(Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(15,Math.abs(end[0]-start[0])),Math.max(15,Math.abs(end[1]-start[1])));start=null;};
canvas.onpointercancel=()=>{start=null;};
$('reportForm').onsubmit = async e => {
  e.preventDefault(); if(!image) {toast('请先选择并遮挡照片');return;}
  $('submit').disabled = true; $('formMessage').textContent = '正在提交…';
  try { const d=Object.fromEntries(new FormData(e.target)); d.photo=canvas.toDataURL('image/png').split(',')[1];d.consent=$('consent').checked;const result=await api('/api/reports',d); toast(`${result.message}，编号：${result.id}`); e.target.reset();image=null;canvas.hidden=true;$('resetPhoto').hidden=true;$('kind').onchange();$('formDialog').close();$('formMessage').textContent=''; } catch(err) {$('formMessage').textContent=err.message;} finally {$('submit').disabled=false;}
};
load().catch(e => {$('count').textContent='加载失败';$('cards').replaceChildren(el('p','暂时无法连接，请刷新重试。'));toast(e.message);});
