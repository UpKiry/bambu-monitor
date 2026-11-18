'use strict';
const $ = id => document.getElementById(id);
const capability = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
let state = null, renderKey = null, choosingRegion = false, submitting = false;
let configInitialized = false, pending = null, reported = null;
const phaseNames = {idle:'待命',working:'处理中',starting:'正在启动',collecting:'采集中',stopping:'正在停止',closing:'正在退出',closed:'已退出'};
const reasons = {
  unconfigured:'请选择区域并登录。', code_required:'需要验证码。请查看短信或邮件，或手动发送一次。',
  credential_rejected:'登录未通过，请检查账号、密码或验证码。', stored_token_unavailable:'本机没有可用登录，请重新登录。',
  login_required:'请输入账号和密码或验证码。', invalid_request:'输入格式不正确，请检查后重试。', busy:'正在执行其他操作，请稍候。',
  rate_limited:'云服务限制了请求频率，请稍后手动重试。', permission_denied:'云服务拒绝访问，请检查账号区域和权限。',
  mfa_unsupported:'此账号需要当前尚不支持的多因素验证。', no_bound_devices:'账号当前没有绑定打印机，请在 Bambu 应用中检查。',
  selected_device_unavailable:'原设备不在绑定列表，请重新选择。', settings_not_confirmed:'请完成或取消当前设置，再开始采集。',
  settings_conflict:'设置已被其他程序修改，请退出服务后重新打开。', cleanup_failed:'连接清理失败，请退出后重新启动服务。',
  keyring_unavailable_session_memory:'系统钥匙串不可用，登录仅留在本次内存，服务重启后需要重新登录。',
  configured_session_only:'设置已保存。登录仅留在本次内存，服务重启后需要重新登录。',
  configured:'设置已保存。', logged_out:'已移除此电脑的登录令牌。', stopped:'采集已停止。', stopping:'已请求停止，正在等待连接清理。',
  authenticated:'登录成功。', select_printer:'请选择一台打印机；绑定列表不表示设备在线。', confirm_settings:'请检查打印机并确认保存。',
  credential_store_unavailable:'系统钥匙串不可用，请使用本次内存模式。', credential_revoke_failed:'本机登录令牌删除失败，无法确认已退出登录。',
  mqtt_dependency_unavailable:'缺少 MQTT 依赖，请安装 cloud 依赖。', service_operation_failed:'操作未完成，请检查网络和设置后重试。',
  collector_failed:'采集发生错误，已经停止。', transport_error:'云端请求失败，请检查网络后手动重试。',
  settings_saved_old_credentials_cleanup_failed:'设置已保存，但旧令牌清理失败。请检查系统钥匙串。',
  settings_save_outcome_unknown:'保存结果不明，已停止后续操作。请退出服务并检查设置。'
};
const friendly = reason => reasons[reason] || '操作未完成，请检查网络、账号区域或设置后重试。';
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const button = (text, action, cls='secondary') => `<button type="button" class="${cls}" data-action="${action}">${text}</button>`;
const note = text => { $('notice').textContent = text; };

async function api(path, data) {
  if (!capability) throw Error('缺少本机控制会话，请从托盘菜单“打开本机设置”重新打开。');
  const headers = {'X-Bambu-Local-Session':capability};
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch('/api/' + path, {
    method:data === undefined ? 'GET' : 'POST', headers,
    body:data === undefined ? undefined : JSON.stringify(data),
    credentials:'omit', cache:'no-store', redirect:'error'
  });
  if (!response.ok) throw Error(response.status === 403 ? '本机控制会话已失效，请从托盘菜单重新打开。' : '本机服务请求失败，请检查服务状态。');
  return response.json();
}

function renderWizard(s) {
  const stage = choosingRegion ? 'region' : s.setup_stage;
  const key = JSON.stringify([stage, s.region, s.printer_id, s.model, s.persistent, stage === 'devices' ? s.devices : null]);
  if (key === renderKey) return;
  renderKey = key;
  const root = $('wizard');
  if (stage === 'region') {
    root.innerHTML = '<p class="step">1 / 4 · 选择账号区域</p><p class="muted">请选择 Bambu 账号所属区域，与电脑所在位置无关。</p><div class="actions">' +
      button('中国大陆账号', 'region-cn', '') + button('全球账号', 'region-global') +
      (s.configured ? button('取消设置', 'cancel') : '') + '</div>';
  } else if (stage === 'login') {
    root.innerHTML = `<p class="step">2 / 4 · 登录账号</p><p class="muted">${s.region === 'cn' ? '国区支持 11 位大陆手机号或邮箱。' : '全球区域使用邮箱。'} 密码和验证码不会写入普通设置文件。</p>
      <div class="form-field"><label for="account">${s.region === 'cn' ? '手机号或邮箱' : '邮箱'}</label><input id="account" autocomplete="off" maxlength="512"></div>
      <div class="form-field"><label for="method">登录方式</label><select id="method"><option value="password">密码登录</option><option value="code">验证码登录</option></select></div>
      <div class="form-field"><label for="credential">密码或验证码</label><input id="credential" type="password" autocomplete="off" maxlength="16384"></div>
      <div class="actions">${button('登录', 'login', '')}${button('发送一次验证码', 'send-code')}${button('取消设置', 'cancel')}</div>`;
  } else if (stage === 'devices') {
    root.innerHTML = '<p class="step">3 / 4 · 选择打印机</p><p class="muted">即使只有一台，也需要明确选择。设备绑定不表示当前在线。</p><div class="form-field"><label for="device">绑定的打印机</label><select id="device"><option value="">请选择一台打印机</option>' +
      s.devices.map(d => `<option value="${escape(d.id)}">${escape(d.model || '机型未知')} · 尾号 ${escape(d.id.slice(-4))}</option>`).join('') +
      '</select></div><div class="actions">' + button('继续', 'select', '') + button('刷新绑定列表', 'discover') + button('取消设置', 'cancel') + '</div>';
  } else if (stage === 'confirm') {
    root.innerHTML = `<p class="step">4 / 4 · 确认并保存</p><p class="device">${escape(s.model || '机型未知')} · 尾号 ${escape(s.printer_id.slice(-4))}</p>
      <p class="muted">普通设置只保存区域、设备和策略。密码与验证码不会保存。</p>
      <label class="check"><input id="persistent" type="checkbox" checked> 将登录令牌保存至系统钥匙串</label>
      <p class="hint">钥匙串不可用时只保留本次内存；重启服务后需要重新登录。</p>
      <div class="actions">${button('保存设置', 'confirm', '')}${button('返回选择', 'discover')}${button('取消设置', 'cancel')}</div>`;
  } else {
    root.innerHTML = `<p class="device">${escape(s.model || '已选打印机')} · 尾号 ${escape(s.printer_id.slice(-4))}</p>
      <p class="muted">登录存储：${s.persistent ? '系统钥匙串' : '尚未恢复或仅本次内存'}</p>
      <div class="actions">${button('重新选择打印机', 'restore')}${button('更换账号或区域', 'change-account')}${button('退出本机登录', 'logout', 'danger')}</div>`;
  }
}

function applyStatus(s) {
  state = s;
  $('phase').textContent = phaseNames[s.phase] || '等待';
  $('service-state').textContent = s.phase === 'collecting' ? '后台运行中' : phaseNames[s.phase] || '待命';
  $('telemetry').textContent = s.phase === 'collecting' ? ({online:'已收到目标遥测',offline:'目标遥测暂无有效证据',auth_required:'需要重新登录',connecting:'等待目标遥测'}[s.connection] || '等待目标遥测') : '未采集中';
  $('region-label').textContent = s.region ? (s.region === 'cn' ? '中国大陆' : '全球') : '待设置';
  const labels = [['nozzle_celsius','喷嘴','°C'],['bed_celsius','热床','°C'],['progress_percent','进度','%']];
  $('metrics').innerHTML = labels.map(([key,label,unit]) => {
    const m = s.metrics[key];
    if (!m) return '';
    let value = m.quality === 'unsupported' ? '不支持' : m.value == null ? '等待数据' : escape(m.value) + unit + (m.quality === 'stale' ? '（旧值）' : '');
    return `<span class="metric">${label} ${value}</span>`;
  }).join('');
  renderWizard(s);
  $('settings').hidden = !s.configured || s.setup_stage !== 'home' || choosingRegion;
  if (s.config && !configInitialized) {
    $('live').value = s.config.liveness_seconds;
    $('initial').value = s.config.initial_delay_seconds;
    $('maximum').value = s.config.max_delay_seconds;
    $('jitter').checked = s.config.jitter;
    configInitialized = true;
  }
  const op = s.operation;
  if (op && op.done && op.id !== reported) {
    reported = op.id;
    if (op.command === 'settings/save' && op.ok) configInitialized = false;
    note(op.ok ? (reasons[op.reason] || '操作已完成。') : friendly(op.reason));
    if (op.reason === 'code_required' && $('method')) $('method').value = 'code';
    pending = null;
  }
  if (s.phase === 'collecting' && op && op.command === 'collector/start') note('采集在后台运行，关闭此网页可继续采集。');
  const busy = submitting || s.phase !== 'idle';
  document.querySelectorAll('button,input,select').forEach(el => {
    el.disabled = busy;
  });
  $('start').disabled = busy || s.setup_stage !== 'home' || choosingRegion;
  $('stop').disabled = !['starting','collecting'].includes(s.phase);
}

async function refresh() {
  try { applyStatus(await api('status')); }
  catch (e) {
    $('service-state').textContent = '无法连接本机服务';
    document.querySelectorAll('button,input,select').forEach(el => {el.disabled = true;});
    note(e.message);
  }
}

async function act(path, data={}) {
  if (submitting) return;
  submitting = true;
  if (state) applyStatus(state);
  note(path === 'collector/stop' ? '正在请求停止…' : '处理中…');
  try {
    const response = await api(path, data);
    if (!response.ok) note(friendly(response.reason));
    else if (response.operation_id) pending = response.operation_id;
    else note(reasons[response.reason] || '请求已收到。');
  } catch (e) { note(e.message); }
  finally { submitting = false; await refresh(); }
}

document.addEventListener('click', async event => {
  const target = event.target.closest('button[data-action]');
  if (!target || target.disabled) return;
  const action = target.dataset.action;
  if (action === 'change-account') {choosingRegion = true; renderKey = null; applyStatus(state); return;}
  if (action.startsWith('region-')) {
    choosingRegion = false;
    await act('setup/begin', {region:action.slice(7)});
  } else if (action === 'login') {
    const account = $('account').value.trim(), credential = $('credential').value, method = $('method').value;
    $('credential').value = '';
    await act('setup/login', {account,credential,method});
  } else if (action === 'send-code') {
    await act('setup/send-code', {account:$('account').value.trim()});
  } else if (action === 'select') {
    await act('setup/select', {printer_id:$('device').value});
  } else if (action === 'confirm') {
    await act('setup/confirm', {persistent:$('persistent').checked});
  } else if (action === 'cancel') {
    choosingRegion = false; renderKey = null;
    await act('setup/cancel');
  } else if (action === 'save-settings') {
    await act('settings/save', {liveness_seconds:Number($('live').value),initial_delay_seconds:Number($('initial').value),max_delay_seconds:Number($('maximum').value),jitter:$('jitter').checked});
  } else if (action === 'logout') {
    if (confirm('移除此电脑的登录令牌？不会撤销其他设备上的云端登录。')) await act('setup/logout');
  } else if (action === 'start' || action === 'stop') {
    await act('collector/' + action);
  } else if (action === 'discover' || action === 'restore') {
    await act('setup/' + action);
  }
});

async function poll() {await refresh(); setTimeout(poll, 1000);}
poll();
