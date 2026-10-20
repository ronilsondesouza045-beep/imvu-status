'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  device: '',
  nick: null,
  avatar: null,
  history: null,
  watch: [],
  alerts: [],
  hooks: [],
  charts: {},
  ws: null,
  reconnect: null,
};

const TITULOS = {
  painel: ['Painel', 'Visao geral do movimento em tempo real.'],
  avatar: ['Avatar', 'Historico, previsao e looks de um nick.'],
  salas: ['Salas', 'Ranking, ocupacao e diretorio publico.'],
  alertas: ['Alertas', 'Acompanhe avatares e receba avisos.'],
  api: ['API', 'Endpoints publicos para integrar.'],
};

const TIPOS = {
  online: 'Entrou online',
  offline: 'Ficou offline',
  sala: 'Mudou de sala',
  look: 'Trocou de look',
  disponibilidade: 'Mudou a disponibilidade',
  suspeita: 'Possivel modo offline',
};

const esc = (valor) =>
  String(valor ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);

function novoDevice() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function deviceId() {
  let id = localStorage.getItem('imvu.device');
  if (!id || !/^[a-f0-9]{32}$/.test(id)) {
    id = novoDevice();
    localStorage.setItem('imvu.device', id);
  }
  return id;
}

async function api(caminho, opcoes = {}) {
  const config = {
    method: opcoes.method || 'GET',
    headers: { Accept: 'application/json' },
  };
  if (opcoes.body) {
    config.headers['Content-Type'] = 'application/json';
    config.body = opcoes.body;
  }
  const resposta = await fetch(caminho, config);
  let dados = null;
  try { dados = await resposta.json(); } catch (_) { dados = null; }
  if (!resposta.ok) {
    const detalhe = dados && (dados.detail || dados.erro || dados.mensagem);
    throw new Error(detalhe || ('HTTP ' + resposta.status));
  }
  return dados;
}

function fmtData(iso) {
  if (!iso) return '--';
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return String(iso);
  return data.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function fmtHora(iso) {
  if (!iso) return '--';
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return String(iso);
  return data.toLocaleString('pt-BR', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

function fmtMinutos(min) {
  const valor = Number(min) || 0;
  if (valor < 60) return valor + ' min';
  const horas = Math.floor(valor / 60);
  const resto = valor % 60;
  return horas + 'h' + (resto ? ' ' + resto + 'min' : '');
}

function toast(mensagem, erro = false) {
  const el = $('#toast');
  el.textContent = mensagem;
  el.className = 'toast show' + (erro ? ' err' : '');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.className = 'toast'; }, 3800);
}

function setLive(ativo) {
  const dot = $('#live-dot');
  const label = $('#live-label');
  if (!dot || !label) return;
  dot.classList.toggle('on', ativo);
  label.textContent = ativo ? 'ao vivo' : 'reconectando';
}

function irPara(pagina) {
  $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === pagina));
  $$('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + pagina));
  const titulo = TITULOS[pagina] || ['', ''];
  $('#page-title').textContent = titulo[0];
  $('#page-sub').textContent = titulo[1];
  if (pagina === 'painel') carregarPainel();
  if (pagina === 'salas') carregarSalas();
  if (pagina === 'alertas') { carregarWatch(); carregarAlertas(); carregarHooks(); }
}

function configurarNavegacao() {
  $$('#nav button').forEach((botao) => {
    botao.addEventListener('click', () => irPara(botao.dataset.page));
  });
  const params = new URLSearchParams(location.search);
  const pagina = params.get('pagina');
  if (pagina && TITULOS[pagina]) irPara(pagina);
}

function relogio() {
  const agora = new Date();
  $('#clock').textContent = agora.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

async function carregarConfig() {
  try {
    const cfg = await api('/api/config');
    $('#api-version').textContent = 'v' + cfg.versao;
    document.title = 'IMVU Status v' + cfg.versao;
  } catch (_) { /* silencioso */ }
}

function metricas(itens) {
  return itens.map((item) => (
    '<div class="metric"><small>' + esc(item.rotulo) + '</small><strong>' +
    esc(item.valor) + '</strong>' + (item.extra ? '<span>' + esc(item.extra) + '</span>' : '') +
    '</div>'
  )).join('');
}

async function carregarPainel() {
  const alvoDigest = $('#digest-lines');
  const alvoHealth = $('#health-grid');
  const alvoRooms = $('#painel-rooms');
  const alvoHeat = $('#heatmap');

  api('/api/digest').then((dados) => {
    alvoDigest.innerHTML = dados.linhas.map((linha) => '<p>' + esc(linha) + '</p>').join('');
    const ia = $('#digest-ai');
    if (dados.texto_ia) {
      ia.style.display = 'block';
      ia.textContent = dados.texto_ia;
    } else {
      ia.style.display = 'none';
    }
  }).catch(() => {
    alvoDigest.innerHTML = '<p class="empty">Nao foi possivel carregar o resumo.</p>';
  });

  api('/api/health').then((saude) => {
    const coletor = saude.coletor || {};
    alvoHealth.innerHTML = metricas([
      { rotulo: 'Banco', valor: saude.db ? 'ok' : 'falha' },
      { rotulo: 'Coletor', valor: coletor.desligado ? 'off' : (coletor.ciclos || 0) + ' ciclos' },
      { rotulo: 'Consultas', valor: coletor.consultas || 0, extra: 'desde o inicio' },
      { rotulo: 'Conexoes', valor: saude.conexoes || 0, extra: 'painel em tempo real' },
      { rotulo: 'Versao', valor: 'v' + saude.versao },
    ]);
  }).catch(() => {
    alvoHealth.innerHTML = '<p class="empty">Servidor indisponivel.</p>';
  });

  api('/api/rooms/top?horas=24&limite=8').then((dados) => {
    alvoRooms.innerHTML = htmlSalas(dados.salas, 'Sem salas monitoradas ainda.');
  }).catch(() => { alvoRooms.innerHTML = '<p class="empty">Sem dados de salas.</p>'; });

  api('/api/rooms/heatmap?horas=168').then((dados) => {
    desenharHeatmap(alvoHeat, dados);
  }).catch(() => { alvoHeat.innerHTML = '<p class="empty">Sem dados de calor.</p>'; });
}

$('#digest-refresh').addEventListener('click', carregarPainel);

function htmlSalas(salas, vazio) {
  if (!salas || !salas.length) return '<p class="empty">' + esc(vazio) + '</p>';
  return salas.map((sala) => {
    const tendencia = Number(sala.tendencia) || 0;
    const classe = tendencia > 0 ? 'trend-up' : (tendencia < 0 ? 'trend-down' : 'room-meta');
    const sinal = tendencia > 0 ? '+' + tendencia : String(tendencia);
    return (
      '<div class="room-row" data-rid="' + esc(sala.rid) + '">' +
      '<div><div class="room-name">' + esc(sala.nome || 'Sala sem nome') + '</div>' +
      '<div class="room-meta">' + esc(sala.dono || 'dono desconhecido') + ' - media ' + esc(sala.media) +
      ' - pico ' + esc(sala.pico) + '</div></div>' +
      '<div class="room-count">' + esc(sala.online) + '</div>' +
      '<div class="' + classe + '">' + esc(sinal) + '</div>' +
      '</div>'
    );
  }).join('');
}

function desenharHeatmap(alvo, dados) {
  if (!alvo) return;
  if (!dados || !dados.matriz || !dados.matriz.length) {
    alvo.innerHTML = '<p class="empty">Sem dados suficientes.</p>';
    return;
  }
  const maximo = Math.max(1, ...dados.matriz.flat());
  let html = '<div class="heatmap-grid"><div></div>';
  for (let hora = 0; hora < 24; hora += 1) {
    html += '<div class="heat-label">' + (hora % 3 === 0 ? hora + 'h' : '') + '</div>';
  }
  dados.matriz.forEach((linha, dia) => {
    html += '<div class="heat-day">' + esc(dados.dias[dia]) + '</div>';
    linha.forEach((valor, hora) => {
      const intensidade = Math.min(0.92, 0.06 + (Number(valor) / maximo) * 0.86);
      html += '<div class="heat-cell" title="' + esc(dados.dias[dia] + ' ' + hora + 'h: ' + valor) +
        ' pessoas" style="background:rgba(45,212,191,' + intensidade.toFixed(2) + ')"></div>';
    });
  });
  html += '</div><div class="heat-legend"><span>menos</span><span class="heat-scale"></span><span>mais</span></div>';
  alvo.innerHTML = html;
}

function configurarAvatar() {
  $('#buscar-btn').addEventListener('click', () => buscarAvatar($('#nick-input').value));
  $('#nick-input').addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter') buscarAvatar($('#nick-input').value);
  });
  $('#watch-btn').addEventListener('click', alternarWatch);
}

async function buscarAvatar(nick) {
  const alvo = String(nick || '').trim();
  if (!alvo) { toast('Digite um nick.', true); return; }
  const botao = $('#buscar-btn');
  botao.disabled = true;
  botao.textContent = 'Consultando...';
  try {
    const perfil = await api('/api/status/' + encodeURIComponent(alvo));
    state.nick = perfil.nick;
    state.avatar = perfil;
    state.history = null;
    $('#nick-input').value = perfil.nick;
    $('#watch-btn').disabled = false;
    renderPerfil(perfil);
    renderPrevisao(perfil.previsao);
    atualizarBotaoWatch();
    const [historico, looks] = await Promise.allSettled([
      api('/api/avatar/' + encodeURIComponent(perfil.nick) + '/history?horas=168'),
      api('/api/avatar/' + encodeURIComponent(perfil.nick) + '/looks'),
    ]);
    if (historico.status === 'fulfilled') {
      state.history = historico.value;
      renderHistorico(historico.value);
    } else {
      renderHistoricoVazio('Historico ainda sendo formado. Volte em alguns minutos.');
    }
    if (looks.status === 'fulfilled') {
      renderLooks(looks.value.looks || []);
    } else {
      $('#avatar-looks').innerHTML = '<p class="empty">Sem looks registrados.</p>';
    }
  } catch (erro) {
    toast(erro.message, true);
    $('#avatar-card').innerHTML = '<div class="empty big">' + esc(erro.message) + '</div>';
    $('#avatar-metrics').innerHTML = '';
    $('#avatar-timeline').innerHTML = '';
    $('#avatar-rooms').innerHTML = '';
    $('#avatar-looks').innerHTML = '';
  } finally {
    botao.disabled = false;
    botao.textContent = 'Consultar';
  }
}

function renderPerfil(perfil) {
  const imagem = perfil.avatar_3d || perfil.foto_perfil;
  const badges = [];
  badges.push('<span class="badge ' + (perfil.online ? 'online' : 'offline') + '">' +
    (perfil.online ? 'ONLINE' : 'OFFLINE') + '</span>');
  if (perfil.availability) badges.push('<span class="badge">' + esc(perfil.availability) + '</span>');
  if (perfil.aparente_offline) badges.push('<span class="badge warn">possivel modo offline</span>');
  (perfil.marcas || []).forEach((marca) => badges.push('<span class="badge">' + esc(marca) + '</span>'));

  const previsao = perfil.previsao || {};
  const sala = perfil.sala
    ? esc(perfil.sala.nome) + ' (' + esc(perfil.sala.privacidade || '?') + ')'
    : 'nao exposta agora';
  const foto = imagem
    ? '<img class="avatar-img" src="' + esc(imagem) + '" alt="avatar" onerror="this.style.display=\'none\'">'
    : '<div class="avatar-img" style="display:grid;place-items:center;color:#647b95;font-weight:700">' +
      esc((perfil.nick || '?').slice(0, 2).toUpperCase()) + '</div>';

  $('#avatar-card').innerHTML =
    '<div class="profile-top">' + foto +
    '<div><div class="profile-name">' + esc(perfil.display_name || perfil.nick) + '</div>' +
    '<div class="profile-sub">@' + esc(perfil.nick) + ' - CID ' + esc(perfil.cid || '?') + '</div>' +
    badges.join('') + '</div></div>' +
    '<div class="profile-facts">' +
    '<div>Sala atual: <b>' + sala + '</b></div>' +
    '<div>Pais: <b>' + esc(perfil.pais || '?') + '</b> - Membro desde <b>' + esc(perfil.registrado || '?') + '</b></div>' +
    '<div>Seguidores: <b>' + esc(perfil.seguidores ?? '?') + '</b> - Ultima vez: <b>' + esc(fmtData(perfil.ultima_vez || perfil.atualizado_em)) + '</b></div>' +
    (previsao.proxima ? '<div>Previsao: <b>' + esc(fmtHora(previsao.proxima)) + '</b></div>' : '') +
    '</div>' +
    (perfil.tagline ? '<div class="tagline">' + esc(perfil.tagline) + '</div>' : '') +
    '<div class="metrics" style="margin-top:16px">' + metricas([
      { rotulo: 'Eventos', valor: perfil.total_eventos ?? '--' },
      { rotulo: 'Primeira vez', valor: perfil.primeira_vez ? fmtData(perfil.primeira_vez).split(',')[0] : '--' },
      { rotulo: 'Fonte', valor: 'IMVU publico' },
    ]) + '</div>';

  $('#avatar-updated').textContent = 'atualizado ' + fmtData(perfil.atualizado_em);
}

function renderPrevisao(previsao) {
  const alvo = $('#avatar-prediction');
  if (!previsao || !previsao.proxima) {
    alvo.innerHTML = '<p class="empty">' +
      esc(previsao && previsao.aviso ? previsao.aviso : 'Sem dados suficientes para prever.') + '</p>';
    return;
  }
  const confianca = Math.round((Number(previsao.confianca) || 0) * 100);
  const janelas = (previsao.janelas || []).map((janela) => (
    '<div class="window-row"><span>' + esc(janela.dia_semana) + ' as ' + esc(janela.hora) + 'h</span>' +
    '<span>' + Math.round((Number(janela.forca) || 0) * 100) + '%</span></div>'
  )).join('');
  alvo.innerHTML =
    '<div class="prediction-main">Provavel em ' + esc(fmtHora(previsao.proxima)) + '</div>' +
    '<div class="prediction-bar"><span style="width:' + Math.max(4, confianca) + '%"></span></div>' +
    '<div class="hint">confianca ' + confianca + '% - ' + esc(previsao.amostras || 0) + ' amostras</div>' +
    (janelas ? '<div class="window-list">' + janelas + '</div>' : '');
}

function renderHistorico(dados) {
  const stats = dados.estatisticas || {};
  $('#avatar-metrics').innerHTML = metricas([
    { rotulo: 'Tempo online', valor: fmtMinutos(stats.minutos_online), extra: 'ultimos 7 dias' },
    { rotulo: 'Recorde online', valor: fmtMinutos(stats.recorde_online_min) },
    { rotulo: 'Recorde offline', valor: fmtMinutos(stats.recorde_offline_min) },
    { rotulo: 'Ultima vez', valor: fmtData(stats.ultima_vez) },
    { rotulo: 'Sala favorita', valor: stats.sala_mais_frequentada ? stats.sala_mais_frequentada.nome : '--' },
  ]);
  renderPrevisao(dados.previsao);

  const dias = (dados.grafico && dados.grafico.dias) || [];
  desenharGrafico('chart-dias', 'bar', dias.map((d) => d.dia.slice(5)), [{
    label: 'minutos',
    data: dias.map((d) => d.minutos),
    backgroundColor: 'rgba(45,212,191,.55)',
    borderColor: '#2dd4bf',
    borderWidth: 1,
    borderRadius: 4,
  }]);

  const horas = (dados.grafico && dados.grafico.horas) || [];
  desenharGrafico('chart-horas', 'line', horas.map((_, i) => i + 'h'), [{
    label: 'entradas',
    data: horas,
    borderColor: '#38bdf8',
    backgroundColor: 'rgba(56,189,248,.12)',
    fill: true,
    tension: .35,
    pointRadius: 0,
  }]);

  const eventos = dados.eventos || [];
  const alvo = $('#avatar-timeline');
  if (!eventos.length) {
    alvo.innerHTML = '<p class="empty">Sem eventos registrados.</p>';
  } else {
    alvo.innerHTML = eventos.map((evento) => (
      '<div class="timeline-item">' +
      '<span class="timeline-dot ' + esc(evento.tipo) + '"></span>' +
      '<span>' + esc(TIPOS[evento.tipo] || evento.tipo) +
      (evento.sala ? ' <span class="room-meta">- ' + esc(evento.sala) + '</span>' : '') + '</span>' +
      '<span class="timeline-when">' + esc(fmtData(evento.ts)) + '</span>' +
      '</div>'
    )).join('');
  }

  const salas = dados.salas || [];
  $('#avatar-rooms').innerHTML = salas.length
    ? salas.map((sala) => (
      '<div class="room-row" style="cursor:default"><div class="room-name">' + esc(sala.nome) +
      '</div><span class="room-meta">' + esc(sala.vezes) + ' eventos</span><span></span></div>'
    )).join('')
    : '<p class="empty">Sem salas registradas.</p>';
}

function renderHistoricoVazio(mensagem) {
  $('#avatar-metrics').innerHTML = '<p class="empty">' + esc(mensagem) + '</p>';
  $('#avatar-timeline').innerHTML = '<p class="empty">Sem eventos.</p>';
  $('#avatar-rooms').innerHTML = '<p class="empty">Sem dados.</p>';
}

function renderLooks(looks) {
  const alvo = $('#avatar-looks');
  if (!looks.length) {
    alvo.innerHTML = '<p class="empty">Nenhum look registrado ainda.</p>';
    return;
  }
  alvo.innerHTML = looks.map((look) => (
    '<div class="look-card">' +
    (look.imagem
      ? '<img src="' + esc(look.imagem) + '" alt="look" loading="lazy" onerror="this.style.display=\'none\'">'
      : '<div class="look-placeholder">sem imagem</div>') +
    '<div class="look-info">' + esc(look.legenda || 'look') + '<br><span class="room-meta">' +
    esc(fmtData(look.quando)) + '</span></div>' +
    '</div>'
  )).join('');
}

function desenharGrafico(id, tipo, labels, datasets) {
  if (!window.Chart) return;
  const canvas = document.getElementById(id);
  if (!canvas) return;
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(canvas, {
    type: tipo,
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(29,44,64,.5)' }, ticks: { color: '#647b95', maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: 'rgba(29,44,64,.5)' }, ticks: { color: '#647b95' } },
      },
    },
  });
}

async function alternarWatch() {
  if (!state.nick) return;
  const naLista = state.watch.some((w) => String(w.nick).toLowerCase() === String(state.nick).toLowerCase());
  try {
    if (naLista) {
      await api('/api/watch/' + encodeURIComponent(state.nick) + '?device=' + state.device, { method: 'DELETE' });
      toast('Avatar removido da lista.');
    } else {
      await api('/api/watch', {
        method: 'POST',
        body: JSON.stringify({ device: state.device, nick: state.nick, notificar: true }),
      });
      pedirNotificacao();
      toast('Agora voce vai receber alertas de ' + state.nick + '.');
    }
    await carregarWatch();
    atualizarBotaoWatch();
  } catch (erro) {
    toast(erro.message, true);
  }
}

function atualizarBotaoWatch() {
  const botao = $('#watch-btn');
  if (!botao) return;
  const naLista = state.nick && state.watch.some(
    (w) => String(w.nick).toLowerCase() === String(state.nick).toLowerCase()
  );
  botao.textContent = naLista ? 'Acompanhando' : 'Acompanhar';
  botao.className = naLista ? 'primary' : 'ghost';
  botao.disabled = !state.nick;
}

async function carregarWatch() {
  try {
    const dados = await api('/api/watch?device=' + state.device);
    state.watch = dados.avatares || [];
    renderWatch();
    atualizarBotaoWatch();
  } catch (erro) {
    $('#watch-list').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

function renderWatch() {
  const alvo = $('#watch-list');
  if (!alvo) return;
  if (!state.watch.length) {
    alvo.innerHTML = '<p class="empty">Nenhum avatar na lista. Consulte um nick e toque em Acompanhar.</p>';
    return;
  }
  alvo.innerHTML = state.watch.map((avatar) => {
    const sala = avatar.sala ? avatar.sala.nome : 'sem sala exposta';
    const quando = avatar.previsao && avatar.previsao.proxima ? 'provavel ' + fmtHora(avatar.previsao.proxima) : 'sem previsao';
    return '<div class="watch-row">' +
      '<div><b>' + esc(avatar.nick) + '</b>' +
      '<small>' + (avatar.online ? 'online' : 'offline') + ' - ' + esc(sala) + '</small>' +
      '<small>' + esc(quando) + '</small></div>' +
      '<button type="button" class="ghost small" data-remove="' + esc(avatar.nick) + '">Remover</button>' +
      '</div>';
  }).join('');
}

$('#watch-list').addEventListener('click', async (evento) => {
  const botao = evento.target.closest('[data-remove]');
  if (!botao) return;
  const nick = botao.dataset.remove;
  try {
    await api('/api/watch/' + encodeURIComponent(nick) + '?device=' + state.device, { method: 'DELETE' });
    toast('Removido: ' + nick);
    await carregarWatch();
  } catch (erro) { toast(erro.message, true); }
});

async function carregarAlertas() {
  try {
    const dados = await api('/api/alerts?device=' + state.device + '&limite=40');
    state.alerts = dados.alertas || [];
    const alvo = $('#alert-list');
    if (!state.alerts.length) {
      alvo.innerHTML = '<p class="empty">Sem alertas ainda.</p>';
      return;
    }
    alvo.innerHTML = state.alerts.map((alerta) => (
      '<div class="feed-item"><span class="feed-time">' + esc(fmtData(alerta.quando)) + '</span>' +
      '<b>' + esc(alerta.titulo) + '</b>' +
      (alerta.corpo ? '<div>' + esc(alerta.corpo) + '</div>' : '') + '</div>'
    )).join('');
  } catch (erro) {
    $('#alert-list').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

$('#mark-seen').addEventListener('click', async () => {
  const ids = state.alerts.filter((a) => !a.visto).map((a) => a.id);
  if (!ids.length) { toast('Nada para marcar.'); return; }
  try {
    await api('/api/alerts/seen', {
      method: 'POST',
      body: JSON.stringify({ device: state.device, ids }),
    });
    toast('Alertas marcados como vistos.');
    carregarAlertas();
  } catch (erro) { toast(erro.message, true); }
});

function configurarHooks() {
  const kind = $('#hook-kind');
  const wrap = $('#hook-chat-wrap');
  const atualizar = () => { wrap.style.display = kind.value === 'telegram' ? 'flex' : 'none'; };
  kind.addEventListener('change', atualizar);
  atualizar();

  $('#hook-form').addEventListener('submit', async (evento) => {
    evento.preventDefault();
    const eventos = $$('.events input:checked').map((input) => input.value);
    const payload = {
      device: state.device,
      tipo: $('#hook-kind').value,
      url: $('#hook-url').value.trim(),
      chat_id: $('#hook-chat').value.trim() || null,
      eventos,
    };
    try {
      await api('/api/webhooks', { method: 'POST', body: JSON.stringify(payload) });
      toast('Webhook adicionado.');
      $('#hook-url').value = '';
      $('#hook-chat').value = '';
      carregarHooks();
    } catch (erro) { toast(erro.message, true); }
  });

  $('#hook-list').addEventListener('click', async (evento) => {
    const botao = evento.target.closest('[data-hook]');
    if (!botao) return;
    try {
      await api('/api/webhooks/' + botao.dataset.hook + '?device=' + state.device, { method: 'DELETE' });
      toast('Webhook removido.');
      carregarHooks();
    } catch (erro) { toast(erro.message, true); }
  });
}

async function carregarHooks() {
  try {
    const dados = await api('/api/webhooks?device=' + state.device);
    state.hooks = dados.webhooks || [];
    const alvo = $('#hook-list');
    if (!state.hooks.length) {
      alvo.innerHTML = '<p class="empty">Nenhum webhook configurado.</p>';
      return;
    }
    alvo.innerHTML = state.hooks.map((hook) => (
      '<div class="watch-row"><div><b>' + esc(hook.tipo) + '</b>' +
      '<small>' + esc(hook.url) + '</small>' +
      '<small>' + (hook.eventos.length ? hook.eventos.join(', ') : 'todos os eventos') + '</small></div>' +
      '<button type="button" class="ghost small danger" data-hook="' + esc(hook.id) + '">Excluir</button></div>'
    )).join('');
  } catch (erro) {
    $('#hook-list').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

function configurarSalas() {
  $('#rid-btn').addEventListener('click', () => abrirSala($('#rid-input').value));
  $('#rid-input').addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter') abrirSala($('#rid-input').value);
  });
  const select = $('#letra-select');
  const letras = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.split('');
  select.innerHTML = letras.map((letra) => '<option value="' + letra + '">' + letra + '</option>').join('');
  $('#letra-btn').addEventListener('click', () => carregarDiretorio(select.value));
  $('#rooms-table').addEventListener('click', (evento) => {
    const linha = evento.target.closest('[data-rid]');
    if (linha) abrirSala(linha.dataset.rid);
  });
  $('#directory').addEventListener('click', (evento) => {
    const chip = evento.target.closest('[data-rid]');
    if (chip) abrirSala(chip.dataset.rid);
  });
}

async function carregarSalas() {
  try {
    const dados = await api('/api/rooms/top?horas=24&limite=20');
    $('#rooms-table').innerHTML = htmlSalas(dados.salas, 'Nenhuma sala monitorada ainda.');
  } catch (erro) {
    $('#rooms-table').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
  try {
    const calor = await api('/api/rooms/heatmap?horas=168');
    desenharHeatmap($('#heatmap-salas'), calor);
  } catch (_) { /* opcional */ }
}

async function abrirSala(rid) {
  const valor = String(rid || '').trim();
  if (!valor) { toast('Informe o ID da sala.', true); return; }
  $('#room-detail').innerHTML = '<div class="skeleton"></div>';
  try {
    const dados = await api('/api/rooms/' + encodeURIComponent(valor) + '/history?horas=24');
    const sala = dados.sala || {};
    const resumo = dados.resumo || {};
    $('#rid-input').value = sala.rid || valor;
    $('#room-detail').innerHTML =
      '<div class="profile-facts">' +
      '<div><b>' + esc(sala.nome || 'Sala') + '</b> - ' + esc(sala.rid || valor) + '</div>' +
      '<div>Dono: <b>' + esc(sala.dono || '?') + '</b> - Capacidade: <b>' + esc(sala.capacidade || '?') + '</b></div>' +
      '<div>Agora: <b>' + esc(resumo.online_agora ?? '?') + '</b> - Media: <b>' + esc(resumo.media ?? '?') +
      '</b> - Pico: <b>' + esc(resumo.pico ?? '?') + '</b></div>' +
      (resumo.melhor_horario ? '<div>Melhor horario: <b>' + esc(resumo.melhor_horario.hora) + 'h</b></div>' : '') +
      '</div>';
    const pontos = dados.pontos || [];
    desenharGrafico('chart-sala', 'line', pontos.map((p) => fmtData(p.ts).slice(-5)), [{
      label: 'online',
      data: pontos.map((p) => p.online),
      borderColor: '#2dd4bf',
      backgroundColor: 'rgba(45,212,191,.12)',
      fill: true,
      tension: .3,
      pointRadius: 0,
    }]);
    if (!pontos.length) {
      $('#room-detail').innerHTML += '<p class="empty">Sala sem amostras ainda.</p>';
    }
  } catch (erro) {
    toast(erro.message, true);
    $('#room-detail').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

async function carregarDiretorio(letra) {
  const alvo = $('#directory');
  alvo.innerHTML = '<div class="skeleton"></div>';
  try {
    const dados = await api('/api/rooms/directory?letra=' + encodeURIComponent(letra));
    const salas = dados.salas || [];
    alvo.innerHTML = salas.length
      ? salas.map((sala) => '<button type="button" class="chip" data-rid="' + esc(sala.rid) + '">' +
        esc(sala.nome) + ' - ' + esc(sala.rid) + '</button>').join('')
      : '<p class="empty">Nenhuma sala encontrada para ' + esc(letra) + '.</p>';
  } catch (erro) {
    alvo.innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

function anexarFeed(evento) {
  const alvo = $('#live-feed');
  if (!alvo) return;
  if (alvo.querySelector('.empty')) alvo.innerHTML = '';
  let texto = '';
  if (evento.tipo === 'status') {
    const acao = (evento.eventos || []).includes('online') ? 'entrou online'
      : (evento.eventos || []).includes('offline') ? 'ficou offline'
      : (evento.eventos || []).includes('sala') ? 'mudou de sala'
      : (evento.eventos || []).includes('look') ? 'trocou de look'
      : 'atualizou';
    texto = '<b>' + esc(evento.nick) + '</b> ' + acao +
      (evento.sala ? ' - ' + esc(evento.sala.nome) : '');
  } else if (evento.tipo === 'sala') {
    texto = 'Sala <b>' + esc(evento.nome || evento.rid) + '</b> com <b>' + esc(evento.online) + '</b> pessoas';
  } else if (evento.tipo === 'alerta') {
    texto = '<b>' + esc(evento.titulo) + '</b>' + (evento.corpo ? ' - ' + esc(evento.corpo) : '');
  } else {
    return;
  }
  const item = document.createElement('div');
  item.className = 'feed-item';
  item.innerHTML = '<span class="feed-time">' + new Date().toLocaleTimeString('pt-BR') + '</span>' + texto;
  alvo.prepend(item);
  while (alvo.children.length > 50) alvo.lastChild.remove();
}

function atualizarPerfilAoVivo(evento) {
  if (!state.nick || String(evento.nick).toLowerCase() !== String(state.nick).toLowerCase()) return;
  if (!state.avatar) return;
  state.avatar.online = evento.online;
  state.avatar.sala = evento.sala;
  const card = $('#avatar-card');
  const badge = card && card.querySelector('.badge.online, .badge.offline');
  if (badge) {
    badge.classList.toggle('online', !!evento.online);
    badge.classList.toggle('offline', !evento.online);
    badge.textContent = evento.online ? 'ONLINE' : 'OFFLINE';
  }
  const fatos = card && card.querySelectorAll('.profile-facts b');
  if (fatos && fatos.length && evento.sala) fatos[0].textContent = evento.sala.nome;
}

function conectarWs() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  let ws;
  try {
    ws = new WebSocket(proto + '://' + location.host + '/ws/live');
  } catch (_) {
    setLive(false);
    setTimeout(conectarWs, 5000);
    return;
  }
  state.ws = ws;
  ws.onopen = () => setLive(true);
  ws.onclose = () => {
    setLive(false);
    clearTimeout(state.reconnect);
    state.reconnect = setTimeout(conectarWs, 4000);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) { /* ignora */ } };
  ws.onmessage = (mensagem) => {
    let evento = null;
    try { evento = JSON.parse(mensagem.data); } catch (_) { return; }
    if (!evento || evento.tipo === 'ping') return;
    anexarFeed(evento);
    if (evento.tipo === 'alerta') {
      notificar(evento.titulo, evento.corpo);
      carregarAlertas();
    }
    if (evento.tipo === 'status') atualizarPerfilAoVivo(evento);
  };
}

function pedirNotificacao() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') Notification.requestPermission();
}

function notificar(titulo, corpo) {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    toast(titulo + (corpo ? ' - ' + corpo : ''));
    return;
  }
  try {
    new Notification(titulo, { body: corpo || '', tag: 'imvu-status', silent: false });
  } catch (_) {
    toast(titulo + (corpo ? ' - ' + corpo : ''));
  }
}

function configurarApi() {
  const rotas = [
    ['GET', '/api/status/{nick}', 'status atual, sala e previsao'],
    ['GET', '/api/avatar/{nick}/history', 'linha do tempo, graficos e recordes'],
    ['GET', '/api/avatar/{nick}/looks', 'galeria de looks'],
    ['GET', '/api/avatar/{nick}/prediction', 'previsao de horarios'],
    ['GET', '/api/rooms/top', 'ranking de salas por ocupacao'],
    ['GET', '/api/rooms/heatmap', 'movimento por dia e hora'],
    ['GET', '/api/rooms/{rid}/history', 'ocupacao de uma sala'],
    ['GET', '/api/rooms/directory?letra=A', 'diretorio publico de salas'],
    ['GET', '/api/digest', 'resumo automatico do dia'],
    ['GET', '/api/watch?device={id}', 'lista de avatares acompanhados'],
    ['POST', '/api/watch', 'adicionar ou atualizar acompanhamento'],
    ['DELETE', '/api/watch/{nick}', 'remover acompanhamento'],
    ['GET', '/api/alerts?device={id}', 'alertas recebidos'],
    ['GET', '/api/events?device={id}', 'stream SSE em tempo real'],
    ['GET', '/api/webhooks?device={id}', 'webhooks configurados'],
    ['POST', '/api/webhooks', 'criar webhook Discord/Telegram'],
    ['GET', '/api/health', 'saude do sistema'],
    ['GET', '/api/config', 'configuracao publica'],
  ];
  $('#api-table').innerHTML = rotas.map((rota) => (
    '<tr><td>' + rota[0] + '</td><td><code>' + esc(rota[1]) + '</code></td><td>' + esc(rota[2]) + '</td></tr>'
  )).join('');
  const base = location.origin;
  $('#api-examples').textContent =
    'curl ' + base + '/api/status/SEU_NICK\n\n' +
    'curl "' + base + '/api/avatar/SEU_NICK/history?horas=168"\n\n' +
    'curl "' + base + '/api/rooms/top?horas=24"\n\n' +
    'curl "' + base + '/api/events?device=' + state.device + '"\n\n' +
    'Documentacao interativa: ' + base + '/docs';
}

function iniciar() {
  state.device = deviceId();
  $('#device-pill').textContent = 'aparelho ' + state.device.slice(0, 6);
  configurarNavegacao();
  configurarAvatar();
  configurarSalas();
  configurarHooks();
  configurarApi();
  relogio();
  setInterval(relogio, 30000);
  carregarConfig();
  carregarPainel();
  carregarWatch();
  conectarWs();
  setInterval(carregarWatch, 45000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', iniciar);
} else {
  iniciar();
}
